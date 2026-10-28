import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.client import Failure
from journey.evidence import Evidence
from journey.resume_checks import check_resubscribe, contains_original


def frame(seq, *, final=False):
    return {'sequence': str(seq), 'result': {'kind': 'status-update', 'taskId': 'task',
            'contextId': 'conversation', 'status': {'state': 'completed' if final else 'working'},
            'metadata': {'eventCursor': seq, 'eventKind': 'message'}, 'final': final}}


class Client:
    def __init__(self, replay, error=None):
        self.replay, self.error, self.calls = replay, error, []

    def stream_rpc(self, selector, params, *, method):
        self.calls.append((selector, params, method))
        if self.error:
            raise self.error
        yield from self.replay


class ResumeChecksTests(unittest.TestCase):
    def execute(self, replay, *, original=None, error=None):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Evidence(Path(tmp))
            originals = original if original is not None else [frame(1), frame(2), frame(3, final=True)]
            for item in originals:
                evidence.append('stream.jsonl', {'phase': 'resume:reply', **item})
            client = Client(replay, error)
            execution = SimpleNamespace(client=client, evidence=evidence, report={}, checkpoint=lambda: None,
                                        conversation_selector=lambda cid: {'agent_id': 'momo'})
            checks = []
            check_resubscribe(execution, {'phase': 'resume:reply', 'task_id': 'task',
                                         'conversation_id': 'conversation'}, checks, 'resume')
            saved = evidence.root / 'resubscribe.jsonl'
            captured = [json.loads(line) for line in saved.read_text().splitlines()] if saved.exists() else []
            return checks, client.calls, captured

    def test_replays_complete_original_content_from_cursor(self):
        replay = [frame(2), frame(3, final=True)]
        replay[0]['result']['metadata']['assistantMessageId'] = 'new-metadata'
        checks, calls, captured = self.execute(replay)
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertEqual(calls[0], ({'agent_id': 'momo'}, {'id': 'task', 'afterSeq': 1}, 'tasks/resubscribe'))
        self.assertEqual(len(captured), 2)

    def test_missing_duplicate_changed_or_unfinished_events_fail(self):
        full = [frame(2), frame(3, final=True)]
        changed = copy.deepcopy(full)
        changed[0]['result']['metadata']['eventKind'] = 'changed'
        unfinished = copy.deepcopy(full)
        unfinished[-1]['result']['final'] = False
        for replay in ([full[1]], [full[0], full[0], full[1]], changed, unfinished):
            with self.subTest(replay=replay):
                checks, _, _ = self.execute(replay)
                self.assertFalse(all(check['passed'] for check in checks))

    def test_product_rejection_and_missing_original_cursor_are_function_failures(self):
        checks, _, _ = self.execute([], error=Failure('http_error', 404))
        self.assertFalse(any(check['passed'] for check in checks))
        self.assertEqual(checks[0]['detail'], 'resubscribe_http_error')
        checks, calls, _ = self.execute([], original=[frame(1)])
        self.assertFalse(any(check['passed'] for check in checks))
        self.assertEqual(calls, [])

    def test_snapshot_comparison_requires_original_values_and_json_types(self):
        self.assertTrue(contains_original({'a': [1, {'b': True}]}, {'a': [1, {'b': True, 'c': 2}]}))
        self.assertFalse(contains_original({'a': [1]}, {'a': [True]}))
        self.assertFalse(contains_original({'a': 1}, {'b': 1}))

    def test_transport_failure_remains_a_framework_error(self):
        with self.assertRaises(Failure) as raised:
            self.execute([], error=Failure('transport_error'))
        self.assertEqual(raised.exception.code, 'transport_error')


if __name__ == '__main__':
    unittest.main()
