import json
import tempfile
import unittest
from pathlib import Path

from journey.diagnostics import analyze_events
from journey.report import mark_expected_tool_errors
from journey.sql_checks import evaluate_sql, evaluate_sql_failure


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

    def test_literal_directory_setup_keeps_the_sql_query_verifiable(self):
        base = "moi-cli -o sql run --db moi --query 'SELECT 17 * 19 AS product' --limit 100 2>&1 | head -40"
        for prefix in ('cd /sandbox/.moi 2>/dev/null || cd .; ', 'cd /tmp; '):
            with self.subTest(prefix=prefix):
                checks, blocked = self.evaluate([self.call(prefix + base), self.result()])
                self.assertTrue(all(item['passed'] for item in checks))
                self.assertIsNone(blocked)
        for prefix in ('cd /tmp || echo fake; ', 'cd /tmp; echo fake; ',
                       'cd /tmp/$(echo x); ', 'cd /tmp && echo fake; '):
            with self.subTest(prefix=prefix):
                checks, blocked = self.evaluate([self.call(prefix + base), self.result()])
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

    def test_expected_query_error_requires_the_actual_failed_call_and_field(self):
        query = 'SELECT missing_column FROM journey_db.orders'
        call = self.call('moi-cli -o sql run --query "' + query + '"')
        result = self.result(exit_code=1)
        result['payload']['output']['output']['stderr'] = 'Unknown column missing_column'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            def check(events):
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                return evaluate_sql_failure(path, 'task', 'sql', query, 'missing_column', 'ws')
            checks, blocked = check([call, result])
            self.assertTrue(checks[0]['passed'])
            self.assertEqual(checks[0]['expected_tool_errors'],
                             [{'task_id': 'task', 'call_id': 'call', 'kind': 'command_exit', 'code': '1'}])
            self.assertIsNone(blocked)
            result['payload']['output']['output']['stderr'] = 'network timeout'
            checks, blocked = check([call, result])
            self.assertFalse(checks[0]['passed'])
            self.assertIsNone(blocked)
            result['payload']['output']['output']['stderr'] = 'network timeout executing SELECT missing_column'
            checks, _ = check([call, result])
            self.assertFalse(checks[0]['passed'])
            checks, blocked = check([call])
            self.assertFalse(checks[0]['passed'])
            self.assertEqual(blocked, 'sql_failure_unverifiable')

    def test_literal_cd_and_reported_exit_preserve_result_and_expected_error(self):
        good = self.call('cd /sandbox/.moi && moi-cli -o sql run --query '
                         '"SELECT 17 * 19 AS product"; echo "EXIT=$?"')
        success = self.result()
        success['payload']['output']['output']['stdout'] += '\nEXIT=0\n'
        checks, blocked = self.evaluate([good, success])
        self.assertTrue(all(item['passed'] for item in checks))
        self.assertIsNone(blocked)
        success['payload']['output']['output']['stdout'] = success['payload']['output']['output']['stdout'].replace('EXIT=0', 'EXIT=1')
        checks, _ = self.evaluate([good, success])
        self.assertFalse(checks[1]['passed'])

        query = 'SELECT missing_column FROM journey_db.orders'
        call = self.call('cd /sandbox/.moi && moi-cli -o sql run --query "' + query + '"; echo "EXIT=$?"')
        result = self.result()
        result['payload']['output']['output'].update({'stdout': 'EXIT=1\n', 'stderr': json.dumps(
            {'code': '500', 'status': 500, 'message': 'column missing_column does not exist'})})
        call['seq'], result['seq'] = 1, 2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in (call, result)))
            checks, blocked = evaluate_sql_failure(path, 'task', 'sql', query, 'missing_column', 'ws')
            self.assertTrue(checks[0]['passed'])
            self.assertIsNone(blocked)
            analysis = analyze_events(path, [{'task_id': 'task', 'phase': 'sql:invalid', 'event_count': 2}])
            mark_expected_tool_errors(analysis, checks)
            self.assertEqual(analysis['metrics']['failed'], 1)
            self.assertEqual({error['kind'] for error in analysis['errors'][0]['errors']},
                             {'reported_http_error', 'captured_command_exit'})
            self.assertTrue(all(error.get('expected') for error in analysis['errors'][0]['errors']))

        altered = self.call('cd /sandbox/.moi && moi-cli -o sql run --query '
                            '"SELECT 17 * 19 AS product"; echo "EXIT=0"')
        checks, blocked = self.evaluate([altered, success])
        self.assertFalse(checks[0]['passed'])
        self.assertEqual(blocked, 'sql_command_unverifiable')

    def test_reported_exit_label_is_not_hard_coded(self):
        command = ('cd /sandbox/.moi && moi-cli -o sql run --query '
                   '"SELECT 17 * 19 AS product"; echo "EXIT_STATUS=$?"')
        result = self.result()
        result['payload']['output']['output']['stdout'] += '\nEXIT_STATUS=0\n'
        checks, blocked = self.evaluate([self.call(command), result])
        self.assertTrue(all(item['passed'] for item in checks))
        self.assertIsNone(blocked)
        result['payload']['output']['output']['stdout'] = result['payload']['output']['output']['stdout'].replace(
            'EXIT_STATUS=0', 'OTHER=0')
        checks, blocked = self.evaluate([self.call(command), result])
        self.assertFalse(checks[1]['passed'])
        self.assertEqual(blocked, 'sql_result_unverifiable')

    def test_sql_error_redirected_to_stdout_is_still_identified(self):
        query = 'SELECT missing_column FROM journey_db.orders'
        call = self.call('moi-cli -o sql run --query "' + query + '" 2>&1; echo "EXIT=$?"')
        result = self.result()
        response = {'code': '500', 'status': 500, 'message': 'column missing_column does not exist'}
        call['seq'], result['seq'] = 1, 2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            for content in (json.dumps(response), json.dumps(response, indent=2)):
                with self.subTest(content=content):
                    result['payload']['output']['output'].update({'stdout': content + '\nEXIT=1\n', 'stderr': ''})
                    path.write_text(''.join(json.dumps(event) + '\n' for event in (call, result)))
                    checks, blocked = evaluate_sql_failure(path, 'task', 'sql', query, 'missing_column', 'ws')
                    self.assertTrue(checks[0]['passed'])
                    self.assertIsNone(blocked)
                    analysis = analyze_events(path, [{'task_id': 'task', 'phase': 'sql:invalid', 'event_count': 2}])
                    mark_expected_tool_errors(analysis, checks)
                    self.assertEqual(analysis['metrics']['failed'], 1)
                    self.assertTrue(all(error.get('expected') for error in analysis['errors'][0]['errors']))


if __name__ == '__main__':
    unittest.main()
