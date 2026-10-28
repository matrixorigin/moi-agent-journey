import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from journey.sandbox_checks import evaluate_python_hash


class SandboxChecksTests(unittest.TestCase):
    def evaluate(self, events, reply=''):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_python_hash(path, 'task', 'sandbox', 'fresh-input', reply)

    def call(self, command, task='task'):
        return {'kind': 'tool_call', 'task_id': task, 'payload': {'tool_id': 'mcp__moi-tools__bash',
                'call_id': 'call', 'arguments': {'command': command}}}

    def result(self, stdout, *, code=0, truncated=False):
        return {'kind': 'tool_result', 'task_id': 'task', 'payload': {'tool_id': 'mcp__moi-tools__bash',
                'call_id': 'call', 'success': True, 'output': {'output': {'exit_code': code,
                'stdout': stdout, 'output_truncated': truncated}}}}

    def test_python_output_and_final_answer_match_independent_hash(self):
        digest = hashlib.sha256(b'fresh-input').hexdigest()
        call = self.call("python3 -c 'import hashlib; print(hashlib.sha256(b\"fresh-input\").hexdigest())'")
        checks, blocked = self.evaluate([call, self.result(digest + '\n')], digest)
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertIsNone(blocked)
        checks, _ = self.evaluate([call, self.result('wrong')], digest)
        self.assertEqual([check['passed'] for check in checks], [True, False, True])
        checks, _ = self.evaluate([call, self.result(digest)], 'wrong')
        self.assertEqual([check['passed'] for check in checks], [True, True, False])

    def test_other_task_or_unobservable_shell_does_not_prove_execution(self):
        digest = hashlib.sha256(b'fresh-input').hexdigest()
        checks, blocked = self.evaluate([self.call('python3 fresh-input', task='other'), self.result(digest)], digest)
        self.assertFalse(checks[0]['passed'])
        self.assertIsNone(blocked)
        checks, blocked = self.evaluate([self.call('echo python3 fresh-input'), self.result(digest)], digest)
        self.assertFalse(checks[0]['passed'])
        self.assertEqual(blocked, 'sandbox_output_unverifiable')

    def test_missing_or_truncated_output_is_blocked(self):
        call = self.call('python3 -c "import hashlib; hashlib.sha256(b\'fresh-input\')"')
        _, blocked = self.evaluate([call])
        self.assertEqual(blocked, 'sandbox_output_unverifiable')
        _, blocked = self.evaluate([call, self.result('partial', truncated=True)])
        self.assertEqual(blocked, 'sandbox_output_unverifiable')
        checks, blocked = self.evaluate([call, self.result('error', code=1)])
        self.assertFalse(checks[1]['passed'])
        self.assertIsNone(blocked)


if __name__ == '__main__':
    unittest.main()
