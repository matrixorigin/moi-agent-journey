"""Keep one QA conversation across runs; derive witnesses from its real history."""
import json
import re
import signal
import struct
import time
import uuid
import zlib
from urllib.parse import urlencode

from .a2a import final_reply
from .builder_flow import ProductCaseFailure
from .client import Failure, error_record, segment
from .evidence import sha256
from .hello import TERMINAL, discover, task_state
from .knowledge_checks import evaluate_knowledge
from .knowledge_fixture import KnowledgeFixture, KnowledgeFixtureUnavailable
from .run_case import Execution
from .skill_checks import check_skill_call
from .version import finish_version, start_version

CHECKPOINT = 'JOURNEY_PERSISTENT_CHECKPOINT_V1'
MODES = ('chat', 'file', 'image', 'skill', 'knowledge')


def contains_value(reply, value):
    return bool(re.search(r'(?<![A-Za-z0-9_])' + re.escape(value) + r'(?![A-Za-z0-9_])', reply))


def message_text(message):
    parts = message.get('parts')
    if not isinstance(parts, list):
        raise Failure('persistent_history_parts_invalid')
    return '\n'.join(p['text'] for p in parts if isinstance(p, dict) and
                     p.get('kind') == 'text' and isinstance(p.get('text'), str))


def checkpoint_value(text):
    if not text.startswith(CHECKPOINT + '\n'):
        return None
    try:
        value = json.loads(text.split('\n', 2)[1])
        if (set(value) != {'run_id', 'sequence', 'code'} or
                str(uuid.UUID(value['run_id'])) != value['run_id'] or
                type(value['sequence']) is not int or value['sequence'] <= 0 or
                not isinstance(value['code'], str) or not re.fullmatch(r'J2P_[A-F0-9]{32}', value['code'])):
            raise ValueError('invalid checkpoint')
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise Failure('persistent_checkpoint_invalid') from error
    return value


def number_image(path, number):
    """A unique, deterministic PNG fixture, using only the standard library."""
    glyphs = ('111101101101111', '010110010010111', '111001111100111', '111001111001111',
              '101101111001001', '111100111001111', '111100111101111', '111001001001001',
              '111101111101111', '111101111001111')
    width, height, scale = 280, 100, 12
    pixels = bytearray(b'\xff' * (width * height * 3))
    for index, char in enumerate(str(number)):
        for row in range(5):
            for column in range(3):
                if glyphs[int(char)][row * 3 + column] == '1':
                    for dy in range(scale):
                        offset = ((20 + row * scale + dy) * width + 20 + index * 60 + column * scale) * 3
                        pixels[offset:offset + scale * 3] = b'\0' * (scale * 3)
    raw = b''.join(b'\0' + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class PersistentExecution(Execution):
    def conversation_matches(self, detail):
        return (detail.get('id') == self.cid and detail.get('workspace_id') == self.root.rsplit('/', 1)[-1] and
                all(detail.get(key) == value for key, value in self.selector.items()) and detail.get('status') == 'active')

    def history(self, *, offset, limit):
        result = self.client.product('GET', self.conversation_path + '/messages?' + urlencode({
            **self.selector, 'role': 'user', 'limit': limit, 'offset': offset}))
        items, total = result.get('items'), result.get('total')
        if (type(total) is not int or total < 0 or not isinstance(items, list) or
                len(items) != min(limit, max(0, total - offset)) or any(
                    not isinstance(item, dict) or item.get('conversation_id') != self.cid or
                    item.get('role') != 'user' or type(item.get('seq')) is not int for item in items) or
                any(a['seq'] >= b['seq'] for a, b in zip(items, items[1:]))):
            raise Failure('persistent_history_page_invalid')
        return result

    def latest_history(self):
        head = self.history(offset=0, limit=1)
        total = head['total']
        page = self.history(offset=max(0, total - 100), limit=100) if total > 1 else head
        if page['total'] != total:
            raise Failure('persistent_history_changed')
        return page

    def previous_checkpoint(self):
        page = self.latest_history()
        self.baseline_seq = max((item['seq'] for item in page['items']), default=0)
        self.evidence.write('persistent/history-before.json', page)
        end = page['total']
        while True:
            for message in reversed(page['items']):
                value = checkpoint_value(message_text(message))
                if value:
                    self.evidence.write('persistent/previous-checkpoint.json', message)
                    return value
            end = max(0, end - 100)
            if end == 0:
                return None
            page = self.history(offset=max(0, end - 100), limit=min(100, end))

    def record(self, rule, passed, detail=''):
        self.report['validation']['checks'].append({'session': 'persistent', 'rule': rule,
            'passed': bool(passed), 'detail': '' if passed else detail})
        self.checkpoint()

    def attach_conversation(self):
        config = self.case['persistent_conversation']
        if config != {'key': 'long-lived-v1'}:
            raise Failure('invalid_persistent_conversation_config')
        identity = json.dumps([self.client.base_url, self.root, self.selector, config['key'], self.case['id']], sort_keys=True)
        self.cid = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        self.conversation_path = self.root + '/conversations/' + segment(self.cid)
        title = 'journey-' + self.case['id'] + '-persistent-v1'
        self.report['persistent_conversation'] = {'conversation_id': self.cid, 'key': config['key'],
            'continuity': 'not_checked', 'retention': 'pending'}
        try:
            detail = self.client.product('GET', self.conversation_path + '?' + urlencode(self.selector))
            created = False
        except Failure as error:
            if error.code != 'http_error' or error.http_status != 404:
                raise
            # Only an authoritative 404 permits creating this deterministic ID.
            detail = self.client.product('POST', self.root + '/conversations', {
                'id': self.cid, **self.selector, 'title': title, 'status': 'active'})
            created = True
        self.evidence.write('persistent/conversation-before.json', detail)
        valid = self.conversation_matches(detail)
        self.record('固定持久会话身份和状态正确', valid, 'persistent_conversation_identity_or_state_mismatch')
        if not valid:
            raise ProductCaseFailure('persistent_conversation_identity_or_state_mismatch')
        if detail.get('active_task_id') or detail.get('pending_ingress_id'):
            raise Failure('persistent_conversation_busy')
        self.conversations.append({'id': self.cid, 'purpose': 'persistent', 'selector': dict(self.selector), 'cleanup': 'pending'})
        self.report['persistent_conversation']['created'] = created
        self.checkpoint()

    def turn(self, phase, prompt, *, attachments=(), knowledge_ids=None):
        prompt += '\n本次测试运行标识：' + self.report['run_id']
        self.pending_prompt = prompt
        self.pending_phase = 'persistent:' + phase
        entry, task = self.send(self.cid, self.pending_phase, prompt, attachments, knowledge_ids=knowledge_ids)
        self.record(phase + ':有最终回复', bool(final_reply(task).strip()), 'empty_final_reply')
        return entry, final_reply(task)

    def exercise(self, mode):
        attachments, knowledge_ids = [], None
        expected = None
        if mode == 'chat':
            a = self.sequence + 17
            prompt, expected = f'请计算 {a} + 26，只回答结果。', str(a + 26)
        elif mode in {'file', 'image'}:
            self.work.mkdir(parents=True, exist_ok=True)
            path = self.work / ('persistent-input-' + self.report['run_id'] + ('.csv' if mode == 'file' else '.png'))
            if mode == 'file':
                expected = 'DOC_' + uuid.uuid4().hex.upper()
                path.write_text('project,code\nOrion,' + expected + '\n', encoding='utf-8')
                prompt = '请读取本轮附件，回答 Orion 的 code。不要使用以前附件的内容。'
            else:
                expected = str(1000 + uuid.uuid4().int % 9000)
                number_image(path, expected)
                prompt = '请看本轮图片，原样回答图上的四位数字。不要使用以前图片的内容。'
            target = self.evidence.root / 'persistent' / 'artifacts' / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            self.evidence.write('persistent/exercise-source.json', {'mode': mode, 'expected': expected, 'sha256': sha256(path)})
            upload = self.client.upload(self.root, path)
            attachments = [upload]
            self.uploads.append(upload)
            self.evidence.write('uploads.json', self.uploads)
            self.report.setdefault('retained_inputs', []).append({'fixture': path.name,
                'file_id': upload['file_id'], 'reason': 'persistent_conversation_attachment'})
        elif mode == 'skill':
            prompt = '请先实际加载 PDF Skill（moi.agent.momo.skill.pdf），然后用一句话说明它的用途。'
        else:
            self.knowledge = KnowledgeFixture(self, self.case['knowledge_fixture'])
            self.report['stage'] = 'knowledge_fixture'
            self.knowledge.setup()
            knowledge_ids = [str(self.knowledge.state['model_id'])]
            expected = self.knowledge.marker
            prompt = '请从本轮选择的知识库查找 Project Orion 的 release code，回答代码并引用原文来源。'
        self.report['stage'] = 'persistent:exercise'
        entry, reply = self.turn('exercise', prompt, attachments=attachments, knowledge_ids=knowledge_ids)
        self.record('本轮 ' + mode + ' 答复正确', bool(reply.strip()) if expected is None else contains_value(reply, expected),
                    'persistent_exercise_answer_mismatch')
        proof, blocked = [], None
        if mode == 'skill':
            check, blocked = check_skill_call(self.evidence.root / 'events.jsonl', entry['task_id'],
                                             'moi.agent.momo.skill.pdf', 'persistent')
            proof.append(check)
        elif mode == 'knowledge':
            proof, blocked = evaluate_knowledge(self.evidence.root / 'events.jsonl', entry['task_id'],
                'persistent', self.knowledge.state, self.knowledge.marker,
                document=self.knowledge.read('knowledge-document.json'))
        history = self.latest_history()
        self.evidence.write('persistent/exercise-history.json', history)
        messages = [m for m in history['items'] if m.get('task_id') == entry['task_id'] and message_text(m) == self.pending_prompt]
        attached = bool(messages) and all(any(p.get('kind') == 'file' and
            p.get('file', {}).get('uri') == 'moi://files/' + upload['file_id']
            for p in messages[0]['parts']) for upload in attachments)
        self.report['persistent_conversation']['exercise_checks'] = proof
        self.record('本轮输入和实际执行证据可回读', len(messages) == 1 and attached and all(c['passed'] for c in proof),
                    blocked or 'persistent_exercise_evidence_missing')
        if blocked:
            self.report['blocked'] = True

    def cleanup(self):
        self.client.deadline = time.monotonic() + 100
        unresolved = None
        if self.pending_conversation:
            # A reused context contains old tasks. Recover only the current run's
            # exact user message, never "one unknown task" from historical tasks.
            try:
                matches = [m for m in self.latest_history()['items'] if m['seq'] > self.baseline_seq and
                           message_text(m) == self.pending_prompt and m.get('task_id')]
                if len(matches) != 1:
                    raise Failure('persistent_send_outcome_unknown')
                task_id = matches[0]['task_id']
                task = self.client.rpc(self.selector, 'tasks/get', {'id': task_id})
                self.tasks.append({'task_id': task_id, 'conversation_id': self.cid, 'phase': self.pending_phase,
                    'state': task_state(task, self.cid, task_id), 'event_cursor': 0, 'event_count': 0,
                    'event_kinds': {}, 'capture_complete': False})
                self.pending_conversation = None
            except Exception as error:
                self.report['harness_errors'].append(error_record('recover_send', error))
                unresolved, self.pending_conversation = self.pending_conversation, None
        try:
            self.cleanup_tasks()
        finally:
            self.pending_conversation = unresolved
        for conversation in self.conversations:
            try:
                self.collect_artifacts(conversation['id'], task_ids={t['task_id'] for t in self.tasks})
                if unresolved or any(t['state'] not in TERMINAL for t in self.tasks):
                    raise Failure('persistent_current_task_unconfirmed')
                detail = self.client.product('GET', self.conversation_path + '?' + urlencode(self.selector))
                self.evidence.write('persistent/conversation-after.json', detail)
                if not self.conversation_matches(detail):
                    raise Failure('persistent_conversation_retention_unconfirmed')
                conversation['cleanup'] = 'retained'
                self.report['persistent_conversation']['retention'] = 'active'
            except Exception as error:
                conversation['cleanup'] = 'incomplete'
                self.report['harness_errors'].append(error_record('cleanup', error, conversation_id=conversation['id']))

    def run(self):
        self.knowledge = None
        self.report['validation'] = {'checks': []}
        try:
            self.root, self.selector = discover(self.client, self.case['workspace'])
            start_version(self)
            self.attach_conversation()
            previous = self.previous_checkpoint()
            self.sequence = previous['sequence'] + 1 if previous else 1
            mode = MODES[(self.sequence - 1) % len(MODES)]
            state = self.report['persistent_conversation']
            state.update({'sequence': self.sequence, 'mode': mode, 'previous_checkpoint': previous})
            session = {'id': 'persistent', 'conversation_id': self.cid, 'answer_delivered': False}
            self.report['sessions'].append(session)
            self.report['stage'] = 'persistent:recall'
            prompt = ('我们上一次运行最后记录的最新检查编号是什么？只回答完整编号。不要查其他会话或文件。'
                      if previous else '这是持久测试对话的初始化轮次，请回复 PERSISTENT_READY。')
            _, reply = self.turn('recall', prompt)
            missing = previous is None and not state['created']
            correct = contains_value(reply, previous['code'] if previous else 'PERSISTENT_READY') and not missing
            state['continuity'] = ('passed' if correct else 'failed') if previous else (
                'missing_checkpoint' if missing else 'bootstrap')
            rule = '跨运行记住上次检查编号' if previous else '已有会话保留历史检查点' if missing else '首次初始化答复正确'
            self.record(rule, correct,
                        'persistent_checkpoint_missing' if missing else 'persistent_recall_mismatch')
            self.exercise(mode)
            current = {'run_id': self.report['run_id'], 'sequence': self.sequence, 'code': 'J2P_' + uuid.uuid4().hex.upper()}
            self.report['stage'] = 'persistent:checkpoint'
            prompt = CHECKPOINT + '\n' + json.dumps(current, sort_keys=True) + '\n请记住这个最新检查编号，并原样回复 code。'
            entry, reply = self.turn('checkpoint', prompt)
            self.record('确认本次新检查编号', contains_value(reply, current['code']), 'persistent_checkpoint_ack_mismatch')
            after = self.latest_history()
            self.evidence.write('persistent/history-after.json', after)
            matches = [m for m in after['items'] if m.get('task_id') == entry['task_id'] and
                       checkpoint_value(message_text(m)) == current]
            self.record('检查点已写入同一会话的真实用户历史', len(matches) == 1 and matches[0]['seq'] > self.baseline_seq,
                        'persistent_checkpoint_not_persisted')
            state['current_checkpoint'] = current
            session['answer_delivered'] = True
            self.report['status'] = 'passed' if all(c['passed'] for c in self.report['validation']['checks']) else 'failed'
            if self.report['status'] == 'failed':
                self.report['failures'].append({'stage': 'persistent', 'reason': 'acceptance_checks_failed'})
        except KnowledgeFixtureUnavailable as error:
            self.report['status'] = 'failed'
            self.report['blocked'] = True
            self.report['failures'].append({'stage': 'knowledge_fixture', 'reason': str(error)})
        except ProductCaseFailure as error:
            self.report['status'] = 'failed'
            self.report['failures'].append({'stage': self.report['stage'], 'reason': 'acceptance_checks_failed', 'details': str(error)})
        except Exception as error:
            self.record_failure(error)
        finally:
            signal.alarm(0)
            finish_version(self)
            self.finalize()
            if self.knowledge:
                cleanup_started = time.monotonic()
                self.knowledge.cleanup(dependencies_confirmed=self.pending_conversation is None and
                    all(t['state'] in TERMINAL for t in self.tasks))
                self.report['cleanup_seconds'] += round(time.monotonic() - cleanup_started, 3)
                self.checkpoint()
        return self.report
