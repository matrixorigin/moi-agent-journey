import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from journey.sandbox_checks import evaluate_python_hash, evaluate_python_recovery


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

    def test_recovery_requires_an_observed_error_then_a_separate_successful_python_call(self):
        def event(kind, call_id, *, command='', code=0, stdout='', stderr='', success=True):
            payload = {'tool_id': 'mcp__moi-tools__bash', 'call_id': call_id}
            if kind == 'tool_call':
                payload['arguments'] = {'command': command}
            else:
                payload.update({'success': success, 'output': {'output': {'exit_code': code,
                    'stdout': stdout, 'stderr': stderr, 'output_truncated': False}}})
            return {'kind': kind, 'task_id': 'task', 'payload': payload}

        first = [event('tool_call', 'bad', command="python3 -c 'raise ValueError(\"marker\")'"),
                 event('tool_result', 'bad', code=1, stderr='ValueError: marker', success=False)]
        second = [event('tool_call', 'good', command="python3 -c 'print(123 * 47)'"),
                  event('tool_result', 'good', stdout='5781\n')]

        def evaluate(events, *, require_failed_tool=False):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'events.jsonl'
                path.write_text(''.join(json.dumps(item) + '\n' for item in events))
                return evaluate_python_recovery(path, 'task', 'recovery', 'marker', 123, 47,
                                                require_failed_tool=require_failed_tool)

        checks, blocked = evaluate(first + second)
        self.assertEqual([item['passed'] for item in checks], [True, True])
        self.assertIsNone(blocked)
        captured = [first[0], event('tool_result', 'bad', code=0, stderr='ValueError: marker')]
        checks, _ = evaluate(captured + second)
        self.assertEqual([item['passed'] for item in checks], [True, True])
        checks, _ = evaluate(captured + second, require_failed_tool=True)
        self.assertEqual([item['passed'] for item in checks], [False, False])
        checks, _ = evaluate(first + second, require_failed_tool=True)
        self.assertEqual([item['passed'] for item in checks], [True, True])
        checks, blocked = evaluate(second + first)
        self.assertEqual([item['passed'] for item in checks], [True, False])
        self.assertIsNone(blocked)
        checks, blocked = evaluate(first + second[:1])
        self.assertFalse(checks[1]['passed'])
        self.assertEqual(blocked, 'sandbox_recovery_evidence_unverifiable')
        indirect = [event('tool_call', 'good', command='echo python 123 47'), second[1]]
        checks, blocked = evaluate(first + indirect)
        self.assertFalse(checks[1]['passed'])
        self.assertEqual(blocked, 'sandbox_recovery_evidence_unverifiable')


if __name__ == '__main__':
    unittest.main()
