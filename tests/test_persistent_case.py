import json
import tempfile
import time
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from journey.builder_flow import ProductCaseFailure
from journey.catalog import load_case, load_suite
from journey.client import Failure
from journey.evidence import Evidence
from journey.persistent_case import CHECKPOINT, PersistentExecution, checkpoint_value, contains_value, number_image
from journey.report import finish_case
from journey.publish import prepare_publication

ROOT = '/workspaces/test-workspace'
SELECTOR = {'agent_id': 'momo', 'agent_workspace_id': 'system'}


class Backend:
    def __init__(self):
        self.base_url = 'https://qa.example.invalid/newmoi'
        self.requests = 0
        self.deadline = time.monotonic() + 300
        self.conversations, self.messages, self.uploads, self.tasks = {}, [], {}, {}
        self.mutations, self.rpc_calls = [], []
        self.forget = False

    def product(self, method, path, body=None, **_kwargs):
        self.requests += 1
        route, query = urlsplit(path).path, parse_qs(urlsplit(path).query)
        if route == '/system/versions':
            return {'backend_components': [{'component': 'moi-backend', 'version': 'test', 'git_commit': 'abc'}]}
        if method == 'POST':
            self.mutations.append((method, route, body))
            item = {**body, 'workspace_id': 'test-workspace'}
            self.conversations[body['id']] = item
            return item
        if route.endswith('/messages'):
            cid = route.split('/')[-2]
            rows = [m for m in self.messages if m['conversation_id'] == cid]
            offset, limit = int(query['offset'][0]), int(query['limit'][0])
            return {'items': rows[offset:offset + limit], 'total': len(rows)}
        cid = route.split('/')[-1]
        if cid not in self.conversations:
            raise Failure('http_error', http_status=404)
        return self.conversations[cid]

    def upload(self, _root, path):
        file_id = str(uuid.uuid4())
        self.uploads[file_id] = path.read_bytes()
        return {'file_id': file_id, 'original_name': path.name}

    def rpc(self, selector, method, body):
        self.rpc_calls.append((method, body['id']))
        return self.tasks[body['id']]


def send(execution, cid, phase, prompt, attachments=(), **_kwargs):
    client = execution.client
    history = [m for m in client.messages if m['conversation_id'] == cid]
    previous = next((checkpoint_value(m['parts'][0]['text']) for m in reversed(history)
                     if m['parts'][0]['text'].startswith(CHECKPOINT + '\n')), None)
    task_id = str(uuid.uuid4())
    parts = [{'kind': 'text', 'text': prompt}] + [{'kind': 'file', 'file': {
        'uri': 'moi://files/' + item['file_id']}} for item in attachments]
    client.messages.append({'id': str(uuid.uuid4()), 'conversation_id': cid, 'task_id': task_id,
                            'role': 'user', 'seq': len(client.messages) + 1, 'parts': parts})
    if phase.endswith(':recall'):
        reply = 'forgot' if client.forget else previous['code'] if previous else 'PERSISTENT_READY'
    elif phase.endswith(':checkpoint'):
        reply = checkpoint_value(prompt)['code']
    else:
        mode = execution.report['persistent_conversation']['mode']
        if mode == 'chat':
            reply = str(execution.sequence + 43)
        elif mode in {'file', 'image'}:
            reply = json.loads((execution.evidence.root / 'persistent/exercise-source.json').read_text())['expected']
        else:
            reply = 'PDF document guide' if mode == 'skill' else execution.knowledge.marker
    entry = {'task_id': task_id, 'conversation_id': cid, 'phase': phase, 'state': 'completed',
             'trace_id': 'trace-' + task_id, 'event_count': 0, 'event_cursor': 0,
             'event_kinds': {}, 'capture_complete': True}
    execution.tasks.append(entry)
    task = {'kind': 'task', 'id': task_id, 'contextId': cid, 'status': {'state': 'completed',
        'message': {'role': 'agent', 'parts': [{'kind': 'text', 'text': reply}]}}}
    client.tasks[task_id] = task
    return entry, task


class PersistentTests(unittest.TestCase):
    def execution(self, root, backend):
        return PersistentExecution(backend, load_case('J2-132'), Evidence(root / 'J2-132'), root / 'work', 'model')

    def test_two_runs_reuse_the_conversation_without_reinjecting_old_answer(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp, patch('journey.persistent_case.discover', return_value=(ROOT, SELECTOR)), \
                patch.object(PersistentExecution, 'send', send), patch.object(PersistentExecution, 'collect_artifacts'), \
                patch.object(PersistentExecution, 'capture'):
            root = Path(tmp)
            one = self.execution(root / 'one', backend)
            first = one.run()
            self.assertEqual(first['status'], 'passed')
            self.assertEqual(first['persistent_conversation']['continuity'], 'bootstrap')
            witness = first['persistent_conversation']['current_checkpoint']['code']
            start = len(backend.messages)
            two = self.execution(root / 'two', backend)
            second = two.run()
            self.assertEqual(second['status'], 'passed')
            self.assertEqual(second['persistent_conversation']['continuity'], 'passed')
            self.assertEqual(one.cid, two.cid)
            self.assertEqual(second['persistent_conversation']['sequence'], 2)
            self.assertEqual(second['persistent_conversation']['mode'], 'file')
            self.assertNotIn(witness, backend.messages[start]['parts'][0]['text'])
            self.assertEqual(len(second['validation']['checks']), 9)
            self.assertEqual(len(backend.conversations), 1)
            self.assertEqual(len(backend.mutations), 1)
            self.assertEqual(second['conversations'][0]['cleanup'], 'retained')
            final = finish_case('J2-132', root / 'two')
            self.assertEqual(final['functional']['state'], 'pass')
            self.assertIn('跨运行上下文：通过', (root / 'two/J2-132/case-summary.md').read_text())

    def test_forgetting_is_a_failed_check_without_resetting_the_session(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp, patch('journey.persistent_case.discover', return_value=(ROOT, SELECTOR)), \
                patch.object(PersistentExecution, 'send', send), patch.object(PersistentExecution, 'collect_artifacts'), \
                patch.object(PersistentExecution, 'capture'):
            root = Path(tmp)
            one = self.execution(root / 'one', backend); one.run()
            backend.forget = True
            two = self.execution(root / 'two', backend); result = two.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['persistent_conversation']['continuity'], 'failed')
            self.assertEqual(two.cid, one.cid)
            self.assertEqual(result['harness_errors'], [])
            self.assertEqual(len(backend.conversations), 1)

    def test_missing_history_on_an_existing_conversation_cannot_pass_as_bootstrap(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp, patch('journey.persistent_case.discover', return_value=(ROOT, SELECTOR)), \
                patch.object(PersistentExecution, 'send', send), patch.object(PersistentExecution, 'collect_artifacts'), \
                patch.object(PersistentExecution, 'capture'):
            root = Path(tmp)
            one = self.execution(root / 'one', backend); one.run()
            backend.messages.clear()
            two = self.execution(root / 'two', backend); result = two.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['persistent_conversation']['continuity'], 'missing_checkpoint')
            self.assertEqual(two.cid, one.cid)
            self.assertEqual(len(backend.conversations), 1)

    def test_only_not_found_allows_creation_and_identity_changes_are_rejected(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp:
            execution = self.execution(Path(tmp), backend)
            execution.root, execution.selector = ROOT, SELECTOR
            execution.report['validation'] = {'checks': []}
            with patch.object(backend, 'product', side_effect=Failure('http_error', http_status=503)) as request:
                with self.assertRaises(Failure): execution.attach_conversation()
                self.assertEqual([c.args[0] for c in request.call_args_list], ['GET'])
            execution.attach_conversation()
            backend.conversations[execution.cid]['title'] = 'automatic product-generated title'
            execution.attach_conversation()
            backend.conversations[execution.cid]['status'] = 'archived'
            with self.assertRaises(ProductCaseFailure): execution.attach_conversation()
            self.assertEqual(len(backend.mutations), 1)

    def test_historical_checkpoint_is_found_beyond_a_page_and_environments_are_separate(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp:
            execution = self.execution(Path(tmp), backend)
            execution.root, execution.selector = ROOT, SELECTOR
            execution.report['validation'] = {'checks': []}
            execution.attach_conversation()
            current = {'run_id': str(uuid.uuid4()), 'sequence': 7, 'code': 'J2P_' + 'A' * 32}
            backend.messages = [{'conversation_id': execution.cid, 'role': 'user', 'seq': i + 1,
                'parts': [{'kind': 'text', 'text': CHECKPOINT + '\n' + json.dumps(current) if i == 2 else 'ordinary'}]}
                for i in range(250)]
            self.assertEqual(execution.previous_checkpoint(), current)
            self.assertEqual(execution.baseline_seq, 250)
            backend.base_url = 'https://local.example.invalid/newmoi'
            other = self.execution(Path(tmp) / 'local', backend)
            other.root, other.selector = ROOT, SELECTOR
            other.report['validation'] = {'checks': []}
            other.attach_conversation()
            self.assertNotEqual(execution.cid, other.cid)

    def test_lost_response_recovers_only_the_exact_current_message(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp, patch.object(PersistentExecution, 'collect_artifacts'), \
                patch.object(PersistentExecution, 'capture'):
            execution = self.execution(Path(tmp), backend)
            execution.root, execution.selector = ROOT, SELECTOR
            execution.report['validation'] = {'checks': []}
            execution.attach_conversation(); execution.baseline_seq = 5
            execution.pending_prompt = 'unique-current-run'; execution.pending_phase = 'persistent:exercise'
            execution.pending_conversation = execution.cid
            backend.messages = [{'conversation_id': execution.cid, 'role': 'user', 'seq': seq,
                'task_id': tid, 'parts': [{'kind': 'text', 'text': text}]} for seq, tid, text in (
                    (1, 'old-task', 'old prompt'), (6, 'new-task', 'unique-current-run'))]
            backend.tasks['new-task'] = {'kind': 'task', 'id': 'new-task', 'contextId': execution.cid,
                                        'status': {'state': 'completed'}}
            execution.cleanup()
            self.assertEqual([t['task_id'] for t in execution.tasks], ['new-task'])
            self.assertEqual(backend.rpc_calls, [('tasks/get', 'new-task')])
            self.assertIsNone(execution.pending_conversation)

    def test_unknown_send_does_not_cancel_or_recover_an_old_task(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp:
            execution = self.execution(Path(tmp), backend)
            execution.root, execution.selector = ROOT, SELECTOR
            execution.report['validation'] = {'checks': []}
            execution.attach_conversation(); execution.baseline_seq = 5
            execution.pending_prompt = 'current'; execution.pending_phase = 'persistent:exercise'
            execution.pending_conversation = execution.cid
            backend.messages = [{'conversation_id': execution.cid, 'role': 'user', 'seq': 1,
                'task_id': 'old-task', 'parts': [{'kind': 'text', 'text': 'current'}]}]
            execution.cleanup()
            self.assertEqual(execution.tasks, [])
            self.assertEqual(backend.rpc_calls, [])
            self.assertEqual(execution.pending_conversation, execution.cid)
            self.assertTrue(execution.report['harness_errors'])

    def test_busy_conversation_is_not_replaced_or_cancelled(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp:
            execution = self.execution(Path(tmp), backend)
            execution.root, execution.selector = ROOT, SELECTOR
            execution.report['validation'] = {'checks': []}
            execution.attach_conversation()
            backend.conversations[execution.cid]['active_task_id'] = 'prior-active-task'
            with self.assertRaisesRegex(Failure, 'persistent_conversation_busy'):
                execution.attach_conversation()
            self.assertEqual(len(backend.conversations), 1)
            self.assertEqual(backend.rpc_calls, [])

    def test_rotating_inputs_have_independent_image_bytes_and_strict_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.png', Path(tmp) / 'b.png'
            number_image(a, '1234'); number_image(b, '5678')
            self.assertEqual(a.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            self.assertNotEqual(a.read_bytes(), b.read_bytes())
            data = a.read_bytes(); at = 8
            while data[at + 4:at + 8] != b'IDAT': at += 12 + int.from_bytes(data[at:at + 4], 'big')
            length = int.from_bytes(data[at:at + 4], 'big')
            self.assertEqual(len(zlib.decompress(data[at + 8:at + 8 + length])), 100 * (280 * 3 + 1))
        with self.assertRaises(Failure): checkpoint_value(CHECKPOINT + '\n{}')
        self.assertIsNone(checkpoint_value('ordinary message'))
        self.assertEqual(load_suite('journey-2')['concurrency'], 10)

    def test_current_artifact_capture_does_not_require_a_zip_or_download_old_files(self):
        backend = Backend()
        with tempfile.TemporaryDirectory() as tmp:
            execution = self.execution(Path(tmp), backend)
            execution.root, execution.selector = ROOT, SELECTOR
            execution.report['validation'] = {'checks': []}
            execution.attach_conversation()
            with patch.object(backend, 'product', return_value={'items': [
                    {'task_id': 'old-task', 'conversation_id': execution.cid, 'file_name': 'old.zip'}], 'total': 1}):
                self.assertIsNone(execution.collect_artifacts(execution.cid, task_ids={'new-task'}))
            inventory = json.loads((execution.evidence.root / 'artifacts' / execution.cid / 'inventory.json').read_text())
            self.assertEqual(inventory, [])

    def test_exact_numeric_and_history_codes_do_not_match_longer_values(self):
        self.assertTrue(contains_value('编号是 1234。', '1234'))
        self.assertFalse(contains_value('12345', '1234'))
        self.assertFalse(contains_value('prefix_J2P_' + 'A' * 32, 'J2P_' + 'A' * 32))

    def test_generated_image_evidence_survives_the_real_publication_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            execution = self.execution(root / 'source', Backend())
            execution.work = root / 'work'
            execution.sequence = 3
            execution.cid = 'conversation'
            execution.conversation_path = ROOT + '/conversations/conversation'
            execution.selector, execution.root = SELECTOR, ROOT
            execution.report['validation'] = {'checks': []}
            execution.report['persistent_conversation'] = {'mode': 'image'}
            with patch.object(PersistentExecution, 'send', send):
                execution.exercise('image')
            rejected = prepare_publication(root / 'source', root / 'public', [])
            self.assertEqual(rejected, [])
            files = list((root / 'public/J2-132/persistent/artifacts').glob('*.png'))
            self.assertEqual(len(files), 1)
            self.assertTrue(files[0].read_bytes().startswith(b'\x89PNG'))


if __name__ == '__main__':
    unittest.main()
