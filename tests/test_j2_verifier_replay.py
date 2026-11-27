"""Replay minimal, sanitized inputs from three real J2 verifier failures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from journey.message_admission import rpc_rejection
from journey.sql_checks import evaluate_sql, evaluate_sql_failure


FIXTURES = Path(__file__).parent / 'fixtures' / 'j2-verifier'


def fixture(case):
    return json.loads((FIXTURES / (case + '.json')).read_text())


class J2VerifierReplayTests(unittest.TestCase):
    def evaluate(self, case, *, task_id=None, query=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(e) + '\n' for e in case['events']))
            args = (path, task_id or case['task_id'], 'sql', query or case['query'])
            if case['source_file'].startswith('results/J2-13/'):
                return evaluate_sql(*args, 'product', '323', case['workspace_id'])
            return evaluate_sql_failure(*args, 'SELEC', case['workspace_id'], error_kind='syntax')

    def test_j2_13_complete_tail_output_verifies_the_observed_query(self):
        checks, blocked = self.evaluate(fixture('j2-13'))
        self.assertIsNone(blocked)
        self.assertTrue(all(c['passed'] for c in checks))

    def test_j2_93_file_replay_preserves_the_actual_sql_error(self):
        checks, blocked = self.evaluate(fixture('j2-93'))
        self.assertIsNone(blocked)
        self.assertTrue(checks[0]['passed'])
        self.assertEqual(checks[0]['expected_tool_errors'][0]['kind'], 'captured_command_exit')
        self.assertEqual(checks[0]['expected_tool_errors'][0]['code'], '1')

    def test_j2_130_structured_conflict_proves_non_admission(self):
        error = rpc_rejection(fixture('j2-130')['exchange'])
        self.assertIsNotNone(error)
        self.assertEqual(error['code'], -32006)
        self.assertEqual(error['data']['reason'], 'ACTIVE_TURN_CONFLICT')

    def test_sql_replays_cannot_satisfy_a_different_task_or_query(self):
        for name in ('j2-13', 'j2-93'):
            for override in ({'task_id': 'foreign'}, {'query': 'SELECT 1'}):
                with self.subTest(case=name, override=override):
                    checks, _ = self.evaluate(fixture(name), **override)
                    self.assertFalse(any(c['passed'] for c in checks))

    def test_tail_rejects_truncated_or_modified_results(self):
        for defect in ('invalid_json', 'capture_truncated', 'result_truncated', 'wrong_value'):
            with self.subTest(defect=defect):
                case = fixture('j2-13')
                output = case['events'][1]['payload']['output']['output']
                value = json.loads(output['stdout'])
                if defect == 'invalid_json':
                    output['stdout'] = output['stdout'][1:]
                elif defect == 'capture_truncated':
                    output['output_truncated'] = True
                elif defect == 'result_truncated':
                    value['truncated'] = True
                    output['stdout'] = json.dumps(value)
                else:
                    value['result'][0][0]['String'] = '999'
                    output['stdout'] = json.dumps(value)
                checks, _ = self.evaluate(case)
                self.assertFalse(checks[-1]['passed'])

    def test_tail_requires_a_positive_numeric_limit_and_no_extra_command(self):
        for suffix in ('tail -0', 'tail -n other', 'tail -30; echo 323', 'tail -30 | cat'):
            with self.subTest(suffix=suffix):
                case = fixture('j2-13')
                args = case['events'][0]['payload']['arguments']
                args['command'] = args['command'].replace('tail -30', suffix)
                checks, blocked = self.evaluate(case)
                self.assertFalse(checks[0]['passed'])
                self.assertEqual(blocked, 'sql_command_unverifiable')

    def test_file_replay_requires_the_matching_files_and_exit_record(self):
        for defect in ('wrong_file', 'missing_exit', 'no_error'):
            with self.subTest(defect=defect):
                case = fixture('j2-93')
                args = case['events'][0]['payload']['arguments']
                output = case['events'][1]['payload']['output']['output']
                if defect == 'wrong_file':
                    args['command'] = args['command'].replace('cat scratch/tmp/sql_err.txt', 'cat other.txt')
                elif defect == 'missing_exit':
                    output['stdout'] = output['stdout'].replace('EXIT=1', '')
                else:
                    output['stdout'] = 'EXIT=0\n--- STDOUT ---\n{}\n--- STDERR ---\n'
                checks, _ = self.evaluate(case)
                self.assertFalse(checks[0]['passed'])

    def test_busy_conflict_still_requires_matching_rpc_identity_and_domain(self):
        original = fixture('j2-130')['exchange']
        for defect in ('identity', 'domain', 'code', 'reason'):
            with self.subTest(defect=defect):
                exchange = copy.deepcopy(original)
                body = json.loads(exchange['response']['error_body'])
                if defect == 'identity':
                    body['id'] = 'foreign'
                elif defect == 'domain':
                    body['error']['data']['domain'] = 'other'
                elif defect == 'code':
                    body['error']['code'] = -32603
                else:
                    body['error']['data']['reason'] = 'UNKNOWN'
                exchange['response']['error_body'] = json.dumps(body)
                self.assertIsNone(rpc_rejection(exchange))
