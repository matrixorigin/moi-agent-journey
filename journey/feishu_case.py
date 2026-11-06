"""Exercise the real member -> Feishu -> MOI -> Feishu path, retaining every turn."""
import argparse
import hashlib
import json
import os
import secrets
import struct
import time
import urllib.parse
import zlib
from pathlib import Path
from datetime import datetime, timedelta

from .catalog import load_case
from .builder_flow import ProductCaseFailure
from .client import Client, Failure, error_record, segment
from .diagnostics import analyze_events
from .evidence import Evidence, configured_secrets, sha256, utc_now
from .feishu import Feishu, reply_content, member_config
from .hello import TERMINAL, discover_workspace
from .run_case import Execution
from .version import finish_version, start_version


def number_image(code):
    """A fresh, unambiguous OCR fixture; the code never appears in the question."""
    glyphs = ['111101101101111', '010110010010111', '111001111100111', '111001111001111',
              '101101111001001', '111100111001111', '111100111101111', '111001001001001',
              '111101111101111', '111101111001111']
    scale, pad = 12, 24
    width, height = len(code) * 4 * scale + 2 * pad, 5 * scale + 2 * pad
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            cx, cy = (x - pad) // scale, (y - pad) // scale
            digit, column = divmod(cx, 4)
            ink = 0 <= digit < len(code) and column < 3 and 0 <= cy < 5 and glyphs[int(code[digit])][cy * 3 + column] == '1'
            raw.append(0 if ink else 255)
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(bytes(raw))) + chunk(b'IEND', b'')


def group_attachment_permission(scopes):
    """A member grant for reading history does not grant bot inbound group events."""
    allowed = {'im:message.group_msg', 'im:message.group_msg:readonly', 'im:message.group_msg.include_bot:read'}
    return any(scope.get('scope_type', 'tenant') == 'tenant' and scope.get('grant_status') == 1 and
               scope.get('scope_name') in allowed for scope in scopes)


def tool_checks(analysis, *, require_error=False):
    bash_names = {'bash', 'mcp__moi-tools__bash'}
    succeeded = sum(t['succeeded'] for t in analysis['metrics']['by_tool'] if t['tool'] in bash_names)
    checks = [{'session': 'tools', 'rule': '服务端事件证明实际执行了 bash 且执行成功',
               'passed': succeeded > 0, 'detail': '' if succeeded else 'bash_success_not_observed'}]
    if require_error:
        expected = [{'task_id': call['task_id'], 'call_id': call['call_id'], 'kind': item['kind'], 'code': str(item['code'])}
                    for call in analysis.get('errors', []) if call['tool'] in bash_names
                    for item in call['errors'] if item['kind'] == 'command_exit' and str(item.get('code')) == '23'
                    and 'J5_EXPECTED_FAILURE' in item.get('detail', '')]
        checks.append({'session': 'tools', 'rule': '预期的失败工具调用被记录', 'passed': bool(expected),
                       'detail': '' if expected else 'expected_tool_error_not_observed', 'expected_tool_errors': expected})
    return checks


class FeishuExecution(Execution):
    def __init__(self, client, case, evidence, work, model, feishu, target):
        super().__init__(client, case, evidence, work, model)
        self.feishu, self.target = feishu, target
        self.window_start = datetime.fromisoformat(self.report['started_at']) - timedelta(seconds=5)
        self.receipts, self.history_cache = {}, {}
        self.report['channel'] = {'provider': 'feishu', 'chat_id': target['chat_id'], 'turns': [],
                                  'external_conversation_policy': 'retain_dedicated_test_group_and_threads'}
        self.report['validation'] = {'checks': []}
        self.variables = {name: 'V' + secrets.token_hex(6).upper() for name in ('memory', 'replacement', 'head', 'tail')}
        self.variables['input'] = secrets.token_hex(16)
        self.variables['hash'] = hashlib.sha256(self.variables['input'].encode()).hexdigest()
        self.variables['image_code'] = str(100000 + secrets.randbelow(900000))
        amounts = [11 + secrets.randbelow(80) for _ in range(3)]
        self.variables['file_code'] = secrets.token_hex(6).upper()
        self.variables['total'] = str(sum(amounts))
        self.csv = 'name,amount\n' + '\n'.join(f'item-{i},{amount}' for i, amount in enumerate(amounts)) + '\ncode,' + self.variables['file_code'] + '\n'

    def check(self, phase, rule, passed, detail=''):
        check = {'session': phase, 'rule': rule, 'passed': bool(passed), 'detail': '' if passed else detail}
        self.report['validation']['checks'].append(check)
        self.checkpoint()
        return check

    def inventory(self):
        result = []
        for page in range(10):
            query = urllib.parse.urlencode({**self.selector, 'limit': 100, 'offset': len(result)})
            value = self.client.product('GET', self.root + '/agent-runtime-tasks?' + query)
            result.extend(value['items'])
            if len(result) >= value['total']:
                return result
            if not value['items']:
                raise Failure('invalid_task_pagination')
            # The product list is ordered by updated_at DESC, not created_at.
            timestamps = [datetime.fromisoformat(t['updated_at'].replace('Z', '+00:00')) for t in value['items']]
            if timestamps != sorted(timestamps, reverse=True):
                raise Failure('feishu_task_history_not_newest_first')
            if timestamps[-1] < self.window_start:
                return result
        raise Failure('feishu_task_inventory_limit')

    def history(self, task):
        # Only this task's messages are retained, even though the group is persistent.
        tid = task['id']
        if tid not in self.history_cache:
            query = urllib.parse.urlencode({**self.selector, 'limit': 100, 'offset': 0})
            path = self.root + '/conversations/' + segment(task['context_id']) + '/messages?' + query
            values = self.client.product('GET', path)['items']
            if not any(m.get('task_id') == tid for m in values):
                # A persistent group may have more than one history page.
                page = self.client.product('GET', path)
                if page.get('total', 0) > 100:
                    query = urllib.parse.urlencode({**self.selector, 'limit': 100, 'offset': page['total'] - 100})
                    values = self.client.product('GET', self.root + '/conversations/' + segment(task['context_id']) + '/messages?' + query)['items']
            selected = [m for m in values if m.get('task_id') == tid]
            if selected:
                self.history_cache[tid] = selected
                self.evidence.write('tasks/' + tid + '/messages.json', {'items': selected})
            return selected
        return self.history_cache[tid]

    def identify(self, turn):
        matches = []
        for task in self.inventory():
            if task['id'] in self.baseline:
                continue
            messages = self.history(task)
            if turn.get('input_message_id') and any(turn['input_message_id'] in json.dumps(m.get('parts', []))
                    for m in messages if m.get('role') == 'user'):
                self.track_task(task, turn['phase'] + ':attachment', turn['input_message_id'], auxiliary=True)
            if any('请求编号 ' + turn['request_id'] + '。' in part.get('text', '')
                   for m in messages if m.get('role') == 'user'
                   for part in m.get('parts', []) if part.get('kind') == 'text'):
                matches.append(task)
        self.evidence.write('turns/' + turn['phase'].replace(':', '-') + '/task-matches.json',
                            {'request_id': turn['request_id'], 'task_ids': [m['id'] for m in matches]})
        if len(matches) > 1:
            for task in matches:
                self.track_task(task, turn['phase'], turn['message_id'])
            self.check(turn['phase'], '同一成员请求只创建一个 QA task', False, 'duplicate_tasks_for_one_message')
            raise ProductCaseFailure('feishu_duplicate_tasks')
        if not matches:
            return None
        task = matches[0]
        turn['task_id'], turn['conversation_id'] = task['id'], task['context_id']
        return self.track_task(task, turn['phase'], turn['message_id'])

    def track_task(self, task, phase, message_id, *, auxiliary=False):
        entry = next((x for x in self.tasks if x['task_id'] == task['id']), None)
        if entry is None:
            entry = {'task_id': task['id'], 'conversation_id': task['context_id'],
                     'message_id': message_id, 'phase': phase, 'state': task['state'], 'auxiliary': auxiliary,
                     'event_cursor': 0, 'event_count': 0, 'event_kinds': {}, 'capture_complete': False}
            self.tasks.append(entry)
            if not any(c['id'] == task['context_id'] for c in self.conversations):
                self.conversations.append({'id': task['context_id'], 'purpose': 'external_feishu',
                    'selector': dict(self.selector), 'cleanup': 'retained_external_conversation'})
        entry['state'] = task['state']
        self.capture(entry, final=entry['state'] in TERMINAL)
        if entry['state'] in TERMINAL:
            a2a = self.client.rpc(self.selector, 'tasks/get', {'id': task['id']})
            self.evidence.write('tasks/' + task['id'] + '/a2a.json', a2a)
        return entry

    def send_turn(self, session, spec):
        phase = session + ':' + spec['phase']
        request_id = 'J5_' + self.report['run_id'].replace('-', '')[:12].upper() + '_' + phase.replace(':', '_').replace('-', '_').upper()
        variables = {**self.variables, 'request_id': request_id}
        text = spec['prompt'].format_map(variables)
        if spec.get('long_text'):
            text = self.variables['head'] + '\n' + ('这段填充文本无需复述。\n' * 320) + '\n' + self.variables['tail'] + '\n' + text
        turn = {'phase': phase, 'request_id': request_id, 'started_at': utc_now(),
                'started_monotonic': time.monotonic(), 'status': 'running'}
        self.report['channel']['turns'].append(turn)
        self.report['stage'] = phase + ':send'
        self.checkpoint()
        mention = '<at user_id="' + self.target['bot_open_id'] + '">MOI-QA-BOT1</at> '
        body = {'text': mention + '请求编号 ' + request_id + '。' + text}
        kind = 'text'
        if spec.get('rich_text'):
            kind = 'post'
            body = {'zh_cn': {'title': request_id, 'content': [[
                {'tag': 'at', 'user_id': self.target['bot_open_id']},
                {'tag': 'text', 'text': '请求编号 ' + request_id + '。' + text}]]}}
        if spec.get('media'):
            image = spec['media'] == 'image'
            directory = self.work / 'feishu-inputs'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (request_id + ('.png' if image else '.csv'))
            path.write_bytes(number_image(self.variables['image_code']) if image else self.csv.encode())
            key = self.feishu.upload(path, image=image)
            resource = self.feishu.send(self.target['chat_id'], {'image_key' if image else 'file_key': key},
                kind='image' if image else 'file', key=request_id + '-input')
            turn['input_message_id'] = resource['message_id']
            turn['input_file'] = {'name': path.name, 'sha256': sha256(path)}
            destination = self.evidence.root / 'artifacts' / 'inputs' / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
            # The next real member message asks about this independently uploaded attachment.
            body['text'] += ' 文件名：' + path.name
        reply_to = self.receipts.get(spec.get('thread_from', ''), {}).get('message_id')
        if spec.get('thread_from') and not reply_to:
            raise Failure('feishu_thread_anchor_missing')
        receipt = self.feishu.send(self.target['chat_id'], body, kind=kind, key=request_id, reply_to=reply_to)
        self.receipts[phase] = receipt
        turn.update(message_id=receipt['message_id'], create_time=receipt.get('create_time'),
                    thread_id=receipt.get('thread_id'), reply_to=reply_to, spec=spec, variables=variables)
        self.checkpoint()
        return turn

    def finish_turn(self, turn, *, wait_seconds=75):
        phase, spec = turn['phase'], turn['spec']
        deadline = min(self.client.deadline, time.monotonic() + wait_seconds)
        entry = None
        reply = None
        observed = {}
        while time.monotonic() < deadline:
            self.report['stage'] = phase + ':observe'
            messages = self.feishu.messages(self.target['chat_id'], start_time=int(time.time()) - 600)
            if turn.get('thread_id'):
                messages += self.feishu.messages(turn['thread_id'], thread=True)
            messages = {m['message_id']: m for m in messages}.values()
            own = [m for m in messages if m['message_id'] == turn['message_id']]
            if own:
                sender = own[0].get('sender', {})
                if sender.get('sender_type') != 'user' or sender.get('id') != self.target['user_open_id']:
                    raise Failure('feishu_member_sender_mismatch')
                turn['member_sender_verified'] = True
            cards = []
            for m in messages:
                if (m.get('chat_id') != self.target['chat_id'] or m.get('sender', {}).get('sender_type') != 'app'
                        or m['sender'].get('id') != self.target['app_id']):
                    continue
                text, final = reply_content(m)
                if turn.get('create_time') and int(m.get('create_time', '0')) >= int(turn['create_time']):
                    observed[m['message_id']] = {'message_id': m['message_id'], 'text': text, 'final_card': final,
                                                  'thread_id': m.get('thread_id')}
                if turn['request_id'] in text:
                    cards.append((m, text, final))
            if len(cards) > 1:
                turn['duplicate_reply_ids'] = [m['message_id'] for m, _, _ in cards]
                break
            if cards:
                reply, text, final = cards[0]
                turn.update(reply_message_id=reply['message_id'], reply_text=text,
                            final_card=final, reply_thread_id=reply.get('thread_id'))
            entry = self.identify(turn)
            if entry and entry['state'] in TERMINAL:
                if entry['state'] != 'completed' or (reply and turn.get('final_card')):
                    break
            self.checkpoint()
            time.sleep(min(3, max(0, deadline - time.monotonic())))
        turn['e2e_seconds'] = round(time.monotonic() - turn['started_monotonic'], 3)
        turn['observed_bot_messages'] = list(observed.values())
        turn['status'] = 'completed' if entry and entry['state'] == 'completed' else 'failed'
        if entry:
            entry['seconds'] = turn['e2e_seconds']
        self.check(phase, '成员身份发送已由飞书消息回读确认', turn.get('member_sender_verified'), 'member_message_not_verified')
        self.check(phase, '真实入站请求关联到唯一且正常完成的 QA task', entry and entry['state'] == 'completed',
                   'agent_' + entry['state'] if entry else 'no_correlated_qa_task_before_deadline')
        self.check(phase, '目标机器人已回传本轮最终答复', reply and turn.get('final_card') and not turn.get('duplicate_reply_ids'),
                   'missing_final_reply_or_duplicate_replies')
        if spec.get('thread_from'):
            self.check(phase, '答复回到原飞书话题', bool(turn.get('thread_id')) and turn.get('reply_thread_id') == turn['thread_id'],
                       'reply_thread_mismatch')
        for assertion in spec.get('assertions', []):
            expected = assertion['value'].format_map(turn['variables'])
            actual = turn.get('reply_text', '')
            passed = expected in actual if assertion['type'] == 'contains' else expected not in actual
            self.check(phase, assertion['rule'], passed, 'reply_content_mismatch')
        session_id = phase.split(':', 1)[0]
        actual_session = next((s for s in self.report['sessions'] if s['id'] == session_id), None)
        if actual_session is None:
            actual_session = {'id': session_id, 'conversation_id': turn.get('conversation_id')}
            self.report['sessions'].append(actual_session)
        elif turn.get('conversation_id'):
            self.check(phase, '同一外部话题复用同一 QA 会话', actual_session['conversation_id'] == turn['conversation_id'],
                       'external_conversation_changed')
        actual_session.update(answer_delivered=bool(reply and turn.get('final_card')),
                              final_task_id=turn.get('task_id'))
        if entry and entry['state'] != 'completed':
            self.report['failures'].append({'stage': phase, 'reason': 'agent_' + entry['state'],
                                           'task_id': entry['task_id']})
        self.checkpoint()

    def run(self):
        try:
            self.root = discover_workspace(self.client, self.case['workspace'])
            if self.root.rsplit('/', 1)[-1] != self.target['workspace_id']:
                raise Failure('feishu_workspace_identity_mismatch')
            self.selector = {'agent_id': self.target['agent_id'], 'agent_workspace_id': self.target['workspace_id']}
            start_version(self)
            conn = self.client.product('GET', self.root + '/ingress-connections/' + segment(self.target['ingress_connection_id']))
            self.evidence.write('connection.json', conn)
            if (not conn.get('enabled') or conn.get('provider') != 'feishu' or
                    conn.get('target') != {'kind': 'agent', 'workspace_id': self.target['workspace_id'], 'id': self.target['agent_id']} or
                    conn.get('provider_config', {}).get('app_id') != self.target['app_id']):
                raise Failure('feishu_ingress_target_mismatch')
            if conn.get('observed_state') != 'connected':
                self.check('preflight', 'QA 飞书入站长连接已建立', False, 'feishu_ingress_not_connected')
                self.report['status'] = 'failed'
                return
            if any(turn.get('media') for session in self.case['sessions'] for turn in session['turns']):
                scopes = self.feishu.request('GET', '/open-apis/application/v6/scopes', actor='bot')
                self.evidence.write('permission-evidence.json', {'observed_at': utc_now(), **scopes})
                if not group_attachment_permission(scopes.get('scopes', [])):
                    self.report.update(status='failed', blocked=True)
                    self.report['failures'].append({'stage': 'preconditions',
                        'reason': 'feishu_group_attachment_event_permission_missing',
                        'details': 'Separate group image/file messages have no @ mention; the bot needs im:message.group_msg.'})
                    return
            self.baseline = {t['id'] for t in self.inventory()}
            planned = {s['id'] + ':' + t['phase']: (s['id'], t) for s in self.case['sessions'] for t in s['turns']}
            order = self.case.get('feishu', {}).get('order', list(planned))
            if set(order) != set(planned) or len(order) != len(planned):
                raise Failure('invalid_feishu_turn_order')
            queued = []
            for phase in order:
                session, spec = planned[phase]
                turn = self.send_turn(session, spec)
                if self.case.get('feishu', {}).get('burst'):
                    queued.append(turn)
                else:
                    self.finish_turn(turn)
            for turn in queued:
                self.finish_turn(turn)
            if self.case.get('feishu', {}).get('isolated_threads'):
                ids = [s.get('conversation_id') for s in self.report['sessions'] if s['id'].startswith('thread-')]
                self.check('isolation', '两个飞书话题分别绑定独立 QA 会话', len(ids) == 2 and all(ids) and len(set(ids)) == 2,
                           'thread_conversations_not_isolated')
            if self.case.get('feishu', {}).get('require_bash'):
                events = self.evidence.root / 'events.jsonl'
                analysis = analyze_events(events, self.tasks) if events.exists() else {'metrics': {'by_tool': []}}
                self.check_tools(analysis)
            failed = any(not c['passed'] for c in self.report['validation']['checks'])
            self.report['status'] = 'failed' if failed else 'passed'
            if failed:
                self.report['failures'].append({'stage': 'validation', 'reason': 'acceptance_checks_failed'})
        except ProductCaseFailure as error:
            self.report['status'] = 'failed'
            self.report['failures'].append({'stage': self.report['stage'], 'reason': str(error)})
        except Exception as error:
            if isinstance(error, Failure) and error.code == 'deadline_exceeded' and self.report['channel']['turns']:
                self.report['status'] = 'timeout'
                self.report['failures'].append(error_record(self.report['stage'], error))
            else:
                self.report['status'] = 'error'
                self.report['harness_errors'].append(error_record(self.report['stage'], error))
        finally:
            self.report['task_seconds'] = round(time.monotonic() - self.started, 3)
            self.client.deadline = time.monotonic() + 45
            for task in self.tasks:
                try:
                    # These IDs were matched to our unique member request in persisted history.
                    # Leave the external session intact, but stop our own unfinished request.
                    if task['state'] not in TERMINAL:
                        stopped = self.client.rpc(self.selector, 'tasks/cancel', {'id': task['task_id']})
                        task['state'] = stopped['status']['state']
                        while task['state'] not in TERMINAL:
                            time.sleep(min(1, max(0, self.client.deadline - time.monotonic())))
                            stopped = self.client.rpc(self.selector, 'tasks/get', {'id': task['task_id']})
                            task['state'] = stopped['status']['state']
                    self.capture(task, final=True)
                except Exception as error:
                    task['capture_complete'] = False
                    self.report['harness_errors'].append(error_record('final_capture', error, task_id=task['task_id']))
            finish_version(self)
            self.report['capture_complete'] = bool(self.tasks) and all(t['capture_complete'] for t in self.tasks)
            self.report['finished_at'] = utc_now()
            for turn in self.report['channel']['turns']:
                turn.pop('started_monotonic', None)
            self.checkpoint()

    def check_tools(self, analysis):
        self.report['validation']['checks'].extend(tool_checks(analysis,
            require_error=self.case['feishu'].get('require_tool_error', False)))
        self.checkpoint()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case_id')
    parser.add_argument('--work', default='work')
    args = parser.parse_args()
    case = load_case(args.case_id)
    evidence = Evidence(Path('results') / case['id'], configured_secrets())
    try:
        target = member_config()
        expiry = os.environ.get('MOI_J5_FEISHU_ACCESS_EXPIRES_AT')
        if not os.environ.get('MOI_J5_FEISHU_PROFILE') and (
                expiry is None or float(expiry) - time.time() < case['timeout_seconds'] + 60):
            raise Failure('feishu_member_token_budget_insufficient')
        client = Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], case['timeout_seconds'], observer=evidence.observe_transport)
        feishu = Feishu(evidence, deadline=client.deadline, profile=os.environ.get('MOI_J5_FEISHU_PROFILE'),
            binary=os.environ.get('MOI_J5_FEISHU_CLI', 'lark-cli'), access_token=os.environ.get('MOI_J5_FEISHU_ACCESS_TOKEN'),
            app_id=target['app_id'], app_secret=os.environ.get('MOI_J5_FEISHU_APP_SECRET'))
        execution = FeishuExecution(client, case, evidence, args.work, case['model'], feishu, target)
        execution.run()
        return 1 if execution.report['harness_errors'] else 0
    except Exception as error:
        evidence.write('result.json', {'case_id': case['id'], 'title': case['title'], 'workspace': case['workspace'],
            'status': 'error', 'harness_errors': [error_record('configuration', error)], 'tasks': [], 'failures': []})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
