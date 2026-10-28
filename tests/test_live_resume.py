import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.client import Failure
from journey.catalog import load_case
from journey.evidence import Evidence
from journey.live_resume import check_live_resume, live_resume_stream
from journey.report import finish_case
from journey.run_case import Execution, task_outcome_failure


def frame(sequence, *, task='task', context='conversation', kind='lifecycle', final=False):
    return {'sequence': str(sequence), 'result': {'kind': 'status-update', 'taskId': task,
            'contextId': context, 'status': {'state': 'completed' if final else 'working'},
            'metadata': {'eventKind': kind}, 'final': final}}


def task(state):
    return {'kind': 'task', 'id': 'task', 'contextId': 'conversation', 'status': {'state': state}}


class FakeClient:
    def __init__(self, *, before='working', replay=None, error=None):
        self.deadline, self.requests = time.monotonic() + 30, 0
        self.before, self.replay, self.error = before, replay if replay is not None else [frame(2), frame(3, final=True)], error
        self.calls, self.original_closed = [], False
        self.reads = 0

    def stream_rpc(self, selector, params, *, method='message/stream'):
        self.calls.append((method, params))
        if method == 'message/stream':
            try:
                yield frame(1)
            finally:
                self.original_closed = True
        else:
            if not self.original_closed:
                raise AssertionError('initial stream must be closed before reconnecting')
            if self.error:
                raise self.error
            yield from self.replay

    def rpc(self, selector, method, params):
        self.reads += 1
        return task(self.before if self.reads == 1 else 'completed')


class LiveResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = Evidence(self.root)

    def run_stream(self, client):
        execution = SimpleNamespace(client=client, tasks=[], evidence=self.evidence, checkpoint=lambda: None)
        stream = live_resume_stream(execution, {'agent_id': 'momo'}, {'message': 'once'}, 'conversation', 'live:reply')
        first = next(stream)
        entry = {'task_id': 'task', 'conversation_id': 'conversation', 'phase': 'live:reply'}
        execution.tasks.append(entry)
        return execution, entry, stream, first

    def add_events(self, sequences):
        for seq in sequences:
            self.evidence.append('events.jsonl', {'task_id': 'task', 'seq': seq, 'kind': 'lifecycle',
                                                  'a2a_state': 'completed' if seq == sequences[-1] else 'working'})

    def test_disconnects_one_request_then_resubscribes_same_task_and_cursor(self):
        client = FakeClient()
        execution, entry, stream, first = self.run_stream(client)
        self.assertEqual(first['sequence'], '1')
        self.assertEqual([event['sequence'] for event in stream], ['2', '3'])
        self.assertEqual(client.calls, [('message/stream', {'message': 'once'}),
                                        ('tasks/resubscribe', {'id': 'task', 'afterSeq': 1})])
        self.add_events([1, 2, 3])
        checks = []
        self.assertIsNone(check_live_resume(execution, entry, checks, 'live'))
        self.assertTrue(all(check['passed'] for check in checks))

    def test_already_finished_task_is_blocked_without_resubscription(self):
        client = FakeClient(before='completed')
        execution, entry, stream, _ = self.run_stream(client)
        self.assertEqual(list(stream), [])
        self.assertEqual([call[0] for call in client.calls], ['message/stream'])
        self.add_events([1, 2])
        checks = []
        self.assertEqual(check_live_resume(execution, entry, checks, 'live'), 'task_not_running_before_disconnect')
        self.assertFalse(any(check['passed'] for check in checks))

    def test_missing_or_different_persisted_events_do_not_pass(self):
        client = FakeClient(replay=[frame(2), frame(4, final=True)])
        execution, entry, stream, _ = self.run_stream(client)
        list(stream)
        self.add_events([1, 2, 3, 4])
        checks = []
        self.assertIsNone(check_live_resume(execution, entry, checks, 'live'))
        self.assertTrue(checks[1]['passed'])
        self.assertFalse(checks[2]['passed'])

    def test_initial_cursor_must_be_persisted(self):
        client = FakeClient()
        execution, entry, stream, _ = self.run_stream(client)
        list(stream)
        self.add_events([2, 3])
        checks = []
        self.assertIsNone(check_live_resume(execution, entry, checks, 'live'))
        self.assertTrue(checks[1]['passed'])
        self.assertFalse(checks[2]['passed'])

    def test_terminal_state_must_be_persisted_as_completed(self):
        client = FakeClient()
        execution, entry, stream, _ = self.run_stream(client)
        list(stream)
        for seq in (1, 2, 3):
            self.evidence.append('events.jsonl', {'task_id': 'task', 'seq': seq, 'kind': 'lifecycle',
                                                  'a2a_state': 'working'})
        checks = []
        self.assertIsNone(check_live_resume(execution, entry, checks, 'live'))
        self.assertFalse(checks[1]['passed'])
        self.assertTrue(checks[2]['passed'])

    def test_duplicate_or_wrong_task_is_a_function_failure(self):
        for replay, expected in [([frame(2), frame(2, final=True)], 'live_resume_invalid_sequence'),
                                 ([frame(2, task='other')], 'live_resume_identity_mismatch')]:
            with self.subTest(expected=expected):
                client = FakeClient(replay=replay)
                _, _, stream, _ = self.run_stream(client)
                with self.assertRaises(Failure) as raised:
                    list(stream)
                self.assertEqual(raised.exception.code, expected)
                self.assertTrue(task_outcome_failure(raised.exception))
                self.assertEqual(len(client.calls), 2)

    def test_product_stream_violation_is_not_a_framework_failure(self):
        client = FakeClient()
        evidence = Evidence(self.root / 'J2-23')
        execution = Execution(client, load_case('J2-23'), evidence, self.root, 'model')
        execution.tasks.append({'task_id': 'task', 'phase': 'live_resume:reply', 'state': 'canceled',
                                'event_count': 1, 'capture_complete': True})
        evidence.append('events.jsonl', {'task_id': 'task', 'kind': 'lifecycle', 'payload': {}})
        execution.report['capture_complete'] = True
        execution.record_failure(Failure('live_resume_identity_mismatch'))
        execution.checkpoint()
        self.assertEqual(execution.report['status'], 'failed')
        self.assertEqual(execution.report['harness_errors'], [])
        self.assertEqual(execution.report['failures'][0]['reason'], 'live_resume_identity_mismatch')
        result = finish_case('J2-23', self.root)
        self.assertEqual(result['test_status'], 'completed')
        self.assertEqual(result['functional']['state'], 'fail')

    def test_missing_recorder_evidence_is_a_framework_failure(self):
        client = FakeClient()
        execution, entry, stream, _ = self.run_stream(client)
        list(stream)
        with self.assertRaises(Failure) as raised:
            check_live_resume(execution, entry, [], 'live')
        self.assertEqual(raised.exception.code, 'missing_recorded_events')
        self.assertFalse(task_outcome_failure(raised.exception))
        del entry['live_resume']
        with self.assertRaises(Failure) as raised:
            check_live_resume(execution, entry, [], 'live')
        self.assertEqual(raised.exception.code, 'invalid_live_resume_evidence')
        self.assertFalse(task_outcome_failure(raised.exception))

    def test_product_rejection_is_distinct_from_transport_failure(self):
        for original, expected in [(Failure('http_error', 409), 'live_resume_rejected'),
                                   (Failure('transport_error', details={'category': 'dns'}), 'transport_error')]:
            with self.subTest(expected=expected):
                client = FakeClient(error=original)
                _, _, stream, _ = self.run_stream(client)
                with self.assertRaises(Failure) as raised:
                    list(stream)
                self.assertEqual(raised.exception.code, expected)
                self.assertEqual(len(client.calls), 2)

    def test_execution_send_captures_the_resumed_task_and_evidence(self):
        client = FakeClient()
        case = {'id': 'J2-23', 'title': 'resume', 'workspace': 'journey-2', 'timeout_seconds': 300}
        execution = Execution(client, case, self.evidence, self.root, 'model')
        execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'momo'}
        execution.conversations.append({'id': 'conversation', 'selector': execution.selector})
        captures = []
        def capture(entry, *, final=False):
            captures.append((final, [call[0] for call in client.calls]))
            for seq in ([1, 2, 3] if final else [1]):
                self.evidence.append('events.jsonl', {'task_id': 'task', 'seq': seq, 'kind': 'lifecycle',
                                                      'a2a_state': 'completed' if seq == 3 else 'working'})
            entry.update({'trace_id': 'trace', 'capture_complete': final})
        execution.capture = capture
        entry, result = execution.send('conversation', 'live:reply', 'prompt', live_resume=True)
        self.assertEqual(result['status']['state'], 'completed')
        self.assertTrue(entry['capture_complete'])
        checks = []
        self.assertIsNone(check_live_resume(execution, entry, checks, 'live'))
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertEqual([call[0] for call in client.calls], ['message/stream', 'tasks/resubscribe'])
        self.assertEqual(captures, [(True, ['message/stream', 'tasks/resubscribe'])])

    def test_rejected_resubscription_still_captures_and_archives_task(self):
        client = FakeClient(error=Failure('http_error', 409))
        client.product = lambda method, path, body=None: {'status': 'archived'}
        case = {'id': 'J2-23', 'title': 'resume', 'workspace': 'journey-2', 'timeout_seconds': 300}
        execution = Execution(client, case, self.evidence, self.root, 'model')
        execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'momo'}
        execution.conversations.append({'id': 'conversation', 'selector': execution.selector, 'cleanup': 'pending'})
        execution.collect_artifacts = lambda cid: []

        def capture(entry, *, final=False):
            self.evidence.append('events.jsonl', {'task_id': 'task', 'seq': 1, 'kind': 'lifecycle'})
            entry.update({'trace_id': 'trace', 'event_count': 1, 'capture_complete': final})

        execution.capture = capture
        with self.assertRaises(Failure) as raised:
            execution.send('conversation', 'live:reply', 'prompt', live_resume=True)
        execution.record_failure(raised.exception)
        execution.finalize()
        self.assertEqual(execution.report['status'], 'failed')
        self.assertEqual(execution.report['harness_errors'], [])
        self.assertTrue(execution.report['capture_complete'])
        self.assertEqual(execution.tasks[0]['trace_id'], 'trace')
        self.assertEqual(execution.conversations[0]['cleanup'], 'archived')
        self.assertEqual([call[0] for call in client.calls], ['message/stream', 'tasks/resubscribe'])


if __name__ == '__main__':
    unittest.main()
