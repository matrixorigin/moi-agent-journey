import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence
from journey.regeneration import regenerate


class RegenerationClient:
    def __init__(self, *, wrong_lineage=False, source_changed=False, duplicate=False, rejected=False,
                 failed=False, wrong_receipt=False):
        self.deadline = time.monotonic() + 30
        self.wrong_lineage, self.source_changed = wrong_lineage, source_changed
        self.duplicate, self.rejected, self.source_reads = duplicate, rejected, 0
        self.failed, self.wrong_receipt = failed, wrong_receipt
        self.source = {'id': 'source', 'context_id': 'conversation', 'state': 'completed',
                       'manifest_id': 'manifest', 'trace_id': 'trace-source',
                       'turn_input_snapshot_id': 'input-source', 'turn_output_snapshot_id': 'output-source',
                       'turn_output_committed': True}
        self.runtime = {'id': 'new', 'context_id': 'conversation', 'state': 'completed',
                        'manifest_id': 'manifest', 'trace_id': 'trace-new',
                        'turn_input_snapshot_id': 'input-new', 'turn_output_snapshot_id': 'output-new',
                        'turn_output_committed': True,
                        'attempt': {'attempt': 2, 'retry_of_task_id': 'other' if wrong_lineage else 'source',
                                    'same_manifest': True, 'regenerate': True}}

    def product(self, _method, path, _body=None):
        if '/agent-runtime-tasks?' in path:
            return {'items': [{'id': 'source', 'context_id': 'conversation'}], 'total': 1}
        if '/agent-runtime-tasks/source?' in path:
            self.source_reads += 1
            result = dict(self.source)
            if self.source_changed and self.source_reads > 1:
                result['turn_output_snapshot_id'] = 'overwritten'
            return result
        raise AssertionError(path)

    def rpc(self, _selector, method, params):
        if method == 'tasks/get':
            return {'kind': 'task', 'id': 'new', 'contextId': 'conversation',
                    'status': {'state': 'failed' if self.failed else 'completed', 'message': {'role': 'agent', 'parts': [
                        {'kind': 'text', 'text': 'answer'}]}}}
        if self.rejected:
            raise Failure('http_error', 409)
        message = params['message']
        control = message['parts'][0]['data']
        assert set(params) == {'message'}
        assert message['taskId'] == 'source'
        assert control['attempt'] == 2 and control['rerunMode'] == 'same_manifest'
        task_id = 'source' if self.duplicate else 'new'
        return {'kind': 'message', 'role': 'agent', 'messageId': message['messageId'],
                'contextId': 'conversation', 'task': {'id': task_id},
                'parts': [{'kind': 'data', 'data': {'type': 'moi.run.control.result', 'version': 1,
                    'controlId': control['controlId'], 'status': 'created', 'action': 'regenerate',
                    'taskId': task_id, 'contextId': 'other' if self.wrong_receipt else 'conversation'}}]}


class RegenerationTests(unittest.TestCase):
    def execution(self, **options):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        evidence, client = Evidence(Path(tmp.name)), RegenerationClient(**options)
        runner = SimpleNamespace(client=client, root='/workspaces/workspace', model='model', evidence=evidence,
            report={}, checkpoint=lambda: None, pending_conversation=None,
            conversation_selector=lambda _: {'agent_id': 'agent', 'agent_workspace_id': 'system'},
            tasks=[{'task_id': 'source', 'conversation_id': 'conversation', 'state': 'completed'}])
        def capture(entry, *, final=False):
            evidence.write('tasks/' + entry['task_id'] + '/runtime.json', client.runtime)
            entry.update({'trace_id': client.runtime['trace_id'], 'event_count': 1, 'capture_complete': final})
        runner.capture = capture
        return runner

    def test_regeneration_preserves_source_and_identifies_new_task_and_trace(self):
        runner, checks = self.execution(), []
        entry, task = regenerate(runner, 'conversation', 'session', 'session:retry', runner.tasks[0], checks)
        self.assertEqual([item['passed'] for item in checks], [True] * 5)
        self.assertEqual((entry['task_id'], entry['trace_id'], task['status']['state']),
                         ('new', 'trace-new', 'completed'))
        self.assertEqual(len(runner.tasks), 2)
        self.assertIsNone(runner.pending_conversation)

    def test_wrong_lineage_or_overwritten_source_does_not_pass(self):
        for option in ('wrong_lineage', 'source_changed'):
            with self.subTest(option=option):
                runner, checks = self.execution(**{option: True}), []
                regenerate(runner, 'conversation', 'session', 'session:retry', runner.tasks[0], checks)
                self.assertFalse(all(item['passed'] for item in checks))

    def test_rejected_or_duplicate_task_is_not_reported_as_regenerated(self):
        for option in ('duplicate', 'rejected'):
            with self.subTest(option=option):
                runner, checks = self.execution(**{option: True}), []
                with self.assertRaises(ProductCaseFailure):
                    regenerate(runner, 'conversation', 'session', 'session:retry', runner.tasks[0], checks)
                self.assertFalse(all(item['passed'] for item in checks))
                self.assertEqual(len(runner.tasks), 1)
                self.assertIsNone(runner.pending_conversation)

    def test_failed_regeneration_keeps_new_task_and_trace_for_the_report(self):
        runner, checks = self.execution(failed=True), []
        with self.assertRaises(Failure) as caught:
            regenerate(runner, 'conversation', 'session', 'session:retry', runner.tasks[0], checks)
        self.assertEqual(caught.exception.code, 'agent_failed')
        self.assertEqual(runner.tasks[-1]['state'], 'failed')
        self.assertEqual(runner.tasks[-1]['trace_id'], 'trace-new')
        self.assertTrue(runner.tasks[-1]['capture_complete'])
        self.assertIsNone(runner.pending_conversation)

    def test_wrong_receipt_is_a_failed_check_even_when_the_task_completes(self):
        runner, checks = self.execution(wrong_receipt=True), []
        regenerate(runner, 'conversation', 'session', 'session:retry', runner.tasks[0], checks)
        self.assertFalse(checks[2]['passed'])
        self.assertEqual(checks[2]['detail'], 'regeneration_receipt_mismatch')


if __name__ == '__main__':
    unittest.main()
