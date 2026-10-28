import json
import tempfile
import unittest
from pathlib import Path

from journey.web_open_checks import evaluate_open


class WebOpenChecksTests(unittest.TestCase):
    def evaluate(self, events):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_open(path, 'task', 'open', 'https://example.org/file', 'unique-value')

    def call(self, url='https://example.org/file', task='task'):
        return {'kind': 'tool_call', 'task_id': task, 'payload': {'tool_id': 'moi_web_open',
                'call_id': 'call', 'arguments': {'url': url}}}

    def result(self, output, *, success=True):
        return {'kind': 'tool_result', 'task_id': 'task', 'payload': {'tool_id': 'moi_web_open',
                'call_id': 'call', 'success': success, 'output': output}}

    def test_open_must_target_url_and_return_expected_content(self):
        result = self.result({'output': {'ok': True, 'url': 'https://example.org/file', 'content': 'unique-value'}})
        checks, blocked = self.evaluate([self.call(), result])
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertIsNone(blocked)
        for events in ([result], [self.call('https://example.org/other'), result], [self.call(task='other'), result]):
            checks, _ = self.evaluate(events)
            self.assertFalse(any(check['passed'] for check in checks))
        wrong = self.result({'output': {'ok': True, 'url': 'https://example.org/file', 'content': 'wrong'}})
        checks, blocked = self.evaluate([self.call(), wrong])
        self.assertTrue(checks[0]['passed'])
        self.assertFalse(checks[1]['passed'])
        self.assertIsNone(blocked)

    def test_environment_failure_and_truncated_output_are_blocked(self):
        quota = self.result({'output': {'ok': False, 'error_code': 'WEB_UNAVAILABLE'}})
        checks, blocked = self.evaluate([self.call(), quota])
        self.assertEqual(blocked, 'WEB_UNAVAILABLE')
        self.assertFalse(checks[1]['passed'])
        truncated = self.result({'truncated': True, 'output': {'ok': True, 'url': 'https://example.org/file', 'content': 'partial'}})
        _, blocked = self.evaluate([self.call(), truncated])
        self.assertEqual(blocked, 'open_content_truncated')
        _, blocked = self.evaluate([self.call()])
        self.assertEqual(blocked, 'open_result_unavailable')


if __name__ == '__main__':
    unittest.main()
