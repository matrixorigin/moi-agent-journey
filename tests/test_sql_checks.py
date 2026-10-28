import json
import tempfile
import unittest
from pathlib import Path

from journey.sql_checks import evaluate_sql


class SqlEvidenceTests(unittest.TestCase):
    def evaluate(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_sql(path, 'task', 'sql', 'SELECT 17 * 19 AS product', 'product', '323', 'ws')

    def call(self, command, *, task='task', call_id='call'):
        return {'kind': 'tool_call', 'task_id': task, 'payload': {
            'tool_id': 'mcp__moi-tools__bash', 'call_id': call_id, 'arguments': {'command': command}}}

    def result(self, value='323', *, task='task', call_id='call', success=True, exit_code=0):
        response = {'query_id': 'query-1', 'status': 'success', 'columns': [{'name': 'product'}],
                    'result': [[{'String': value, 'Valid': True}]]}
        return {'kind': 'tool_result', 'task_id': task, 'payload': {'call_id': call_id, 'success': success,
                'output': {'output': {'exit_code': exit_code, 'stdout': json.dumps(response)}}}}

    def test_correct_answer_without_a_real_sql_call_does_not_pass(self):
        checks, blocked = self.evaluate([])
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertEqual(checks[0]['detail'], 'sql_call_missing')
        self.assertIsNone(blocked)

    def test_only_the_requested_query_and_its_result_count(self):
        command = "moi-cli -o sql run --query 'SELECT 17 * 19 AS product'"
        checks, blocked = self.evaluate([self.call(command), self.result()])
        self.assertTrue(all(item['passed'] for item in checks))
        self.assertIsNone(blocked)
        checks, _ = self.evaluate([self.call(command), self.result('999')])
        self.assertTrue(checks[0]['passed'])
        self.assertFalse(checks[1]['passed'])
        checks, _ = self.evaluate([self.call(command, task='other'), self.result()])
        self.assertFalse(any(item['passed'] for item in checks))
        checks, _ = self.evaluate([self.call("moi-cli -o sql run --query 'SELECT 1'"), self.result()])
        self.assertEqual(checks[0]['detail'], 'sql_query_mismatch')

    def test_unobservable_query_file_or_result_is_blocked(self):
        checks, blocked = self.evaluate([self.call('moi-cli -o sql run --query-file /tmp/query.sql')])
        self.assertFalse(checks[0]['passed'])
        self.assertEqual(blocked, 'sql_query_file_unverifiable')
        checks, blocked = self.evaluate([self.call("moi-cli -o sql run --query 'SELECT 17*19 AS product'")])
        self.assertTrue(checks[0]['passed'])
        self.assertEqual(blocked, 'sql_result_unverifiable')

    def test_only_direct_sql_arguments_count_not_unrelated_shell_text(self):
        query = 'SELECT 17 * 19 AS product'
        observed = "moi-cli -o sql run --db moi --query-file <(printf 'SELECT 17 * 19 AS product') --limit 100 2>&1 | head -80"
        checks, blocked = self.evaluate([self.call(observed), self.result()])
        self.assertTrue(all(item['passed'] for item in checks))
        self.assertIsNone(blocked)
        for command in (f"echo 'moi-cli sql run --query {query}'",
                        f"moi-cli -o sql run --query 'SELECT 1' # {query}",
                        f"moi-cli -o sql run --workspace other --query '{query}'"):
            with self.subTest(command=command):
                checks, blocked = self.evaluate([self.call(command), self.result()])
                self.assertFalse(checks[0]['passed'])
                self.assertEqual(blocked, 'sql_command_unverifiable')

    def test_numeric_head_limits_preserve_query_evidence(self):
        base = "moi-cli -o sql run --db moi --query 'SELECT 17 * 19 AS product' --limit 10"
        for tail in (' 2>&1 | head -40', ' | head -3', ' 2>&1 | head -n 40'):
            with self.subTest(tail=tail):
                checks, blocked = self.evaluate([self.call(base + tail), self.result()])
                self.assertTrue(all(item['passed'] for item in checks))
                self.assertIsNone(blocked)
        for tail in (' | head -40 ; echo 323', ' | head -n other', ' | cat', ' 2>&1 | head -0'):
            with self.subTest(tail=tail):
                checks, blocked = self.evaluate([self.call(base + tail), self.result()])
                self.assertFalse(checks[0]['passed'])
                self.assertEqual(blocked, 'sql_command_unverifiable')

    def test_direct_workspace_sql_tool_is_also_validated(self):
        call = {'kind': 'tool_call', 'task_id': 'task', 'payload': {'tool_id': 'query_workspace_sql',
                'call_id': 'direct', 'arguments': {'workspace_id': 'ws', 'sql': 'SELECT 17 * 19 AS product'}}}
        result = {'kind': 'tool_result', 'task_id': 'task', 'payload': {'call_id': 'direct', 'success': True,
                  'output': {'output': {'workspace_id': 'ws', 'sql': 'SELECT 17 * 19 AS product',
                  'columns': ['product'], 'rows': [[323]], 'row_count': 1}}}}
        checks, blocked = self.evaluate([call, result])
        self.assertTrue(all(item['passed'] for item in checks))
        self.assertIsNone(blocked)
        call['payload']['arguments']['workspace_id'] = 'other'
        checks, _ = self.evaluate([call, result])
        self.assertFalse(checks[0]['passed'])


if __name__ == '__main__':
    unittest.main()
