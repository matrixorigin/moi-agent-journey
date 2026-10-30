"""Offline attachment contracts; never start a model request to test a message shape."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from journey.catalog import ROOT, load_case, load_suite
from journey.chat_attachments import bind_chat_attachment, validate_chat_attachment
from journey.client import Failure
from journey.evidence import Evidence
from journey.run_case import Execution


class ChatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'method': 'message/stream', 'params': {'message': {
            'contextId': 'independent-conversation', 'parts': [{'kind': 'text', 'text': 'OLD_PASTED_INPUT'}],
            'metadata': {'llm_backend_id': -900001}}, 'model': 'configured-model'}}
        self.task = {'id': 'T001', 'title': '访谈提炼', 'request': '提取需求并引用依据。',
                     'inputs': ['samples/shared/interview.md']}
        self.upload = {'file_id': 'uploaded-fixture', 'original_name': 'samples.zip',
                       'catalog_file': {'workspace_id': 'synthetic-workspace', 'file_id': 'uploaded-fixture'}}

    def test_bound_file_replaces_legacy_pasted_input_and_preserves_routing(self):
        result = bind_chat_attachment(self.payload, self.task, self.upload, 'offline-test')
        message = result['params']['message']
        self.assertNotIn('OLD_PASTED_INPUT', str(result))
        self.assertEqual(message['parts'][1]['file']['uri'], 'moi://files/uploaded-fixture')
        self.assertEqual(message['metadata']['files'], [self.upload])
        self.assertEqual(message['metadata']['llm_backend_id'], -900001)
        self.assertEqual(message['contextId'], 'independent-conversation')
        self.assertEqual(result['params']['model'], 'configured-model')
        self.assertIn(self.task['request'], message['parts'][0]['text'])
        self.assertEqual(self.payload['params']['message']['parts'][0]['text'], 'OLD_PASTED_INPUT')

    def test_absent_mismatched_and_text_only_inputs_are_rejected(self):
        result = bind_chat_attachment(self.payload, self.task, self.upload, 'offline-test')
        for mutate in (
                lambda m: m['parts'].pop(),
                lambda m: m['metadata'].update(files=[]),
                lambda m: m['parts'][1]['metadata'].update(file_id='other'),
                lambda m: m['parts'][1]['file'].update(uri='moi://files/other'),
                lambda m: m['parts'][0].update(text='--- 原文件 a.md 全文开始 ---\nsource content')):
            changed = copy.deepcopy(result)
            mutate(changed['params']['message'])
            with self.assertRaises(ValueError):
                validate_chat_attachment(changed)
        with self.assertRaises(ValueError):
            bind_chat_attachment(self.payload, {**self.task, 'inputs': ['../../private']}, self.upload, 'offline-test')
        with self.assertRaises(ValueError):
            bind_chat_attachment(self.payload, self.task, {'original_name': 'samples.zip'}, 'offline-test')

    def test_all_100_business_task_requests_use_attachments_without_source_pasting(self):
        for case_id in load_suite('journey-4')['cases']:
            case = load_case(case_id)
            task_id = case['source']['task']
            task_file = ROOT / 'cases/journey-4/_materials/samples' / task_id / 'task.md'
            task = {'id': task_id, 'title': case['title'], 'request': task_file.read_text().splitlines()[2],
                    'inputs': [asset['path'] for asset in case['assets']]}
            result = bind_chat_attachment(self.payload, task, self.upload, 'offline-test')
            validate_chat_attachment(result)
            self.assertNotIn('OLD_PASTED_INPUT', str(result))
            self.assertIn(task['request'], result['params']['message']['parts'][0]['text'])

    def test_executor_sends_native_attachment_and_blocks_missing_inputs_before_rpc(self):
        case = load_case('J4-01')
        client = Mock()
        client.requests = 0
        with tempfile.TemporaryDirectory() as directory:
            execution = Execution(client, case, Evidence(Path(directory)), directory, 'configured-model')
            execution.conversations.append({'id': 'cid', 'selector': {'agent_id': 'momo', 'agent_workspace_id': 'system'}})
            with self.assertRaisesRegex(Failure, 'invalid_business_input_attachment'):
                execution.send('cid', 'business:execute', 'Read the attached input.')
            client.stream_rpc.assert_not_called()
            client.stream_rpc.return_value = (item for item in ())
            with self.assertRaisesRegex(Failure, 'stream_ended_without_task'):
                execution.send('cid', 'business:execute', 'Read the attached input.', [self.upload])
            selector, params = client.stream_rpc.call_args.args
            self.assertEqual(selector['agent_id'], 'momo')
            validate_chat_attachment({'params': params})
            self.assertEqual(params['message']['metadata']['files'], [self.upload])
            client.stream_rpc.reset_mock()
            with self.assertRaisesRegex(Failure, 'pasted_input_in_attachment_round'):
                execution.send('cid', 'business:execute', '全文直接提供在下面', [self.upload])
            client.stream_rpc.assert_not_called()
