import json
import tempfile
import unittest
from pathlib import Path

from journey.knowledge_checks import evaluate_knowledge, evaluate_knowledge_table


class KnowledgeEvidenceTests(unittest.TestCase):
    def evaluate(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_knowledge(path, 'task', 'query', {'model_id': 42, 'file_id': 'file',
                                      'source_id': 'source'}, 'unique-marker')

    def events(self):
        return [
            {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'search_rag_chunks', 'call_id': 'call'}},
            {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'search_rag_chunks',
                                                                    'call_id': 'call', 'success': True}},
            {'task_id': 'task', 'kind': 'artifact', 'payload': {'tool_id': 'search_rag_chunks', 'call_id': 'call',
                'artifact': {'artifact_id': 'retrieval', 'data': {'chunks': [{'semantic_model_id': 42,
                    'file_id': 'file', 'source_row_id': 'source', 'chunk_id': 'chunk', 'content': 'unique-marker'}]}}}},
            {'task_id': 'task', 'kind': 'artifact', 'payload': {'artifact': {'metadata': {
                'matrixflow_type': 'knowledge.answer', 'source_refs': [{'type': 'rag_chunk', 'semantic_model_id': 42,
                    'file_id': 'file', 'source_row_id': 'source', 'chunk_id': 'chunk', 'artifact_id': 'retrieval'}]}}}},
        ]

    def test_retrieval_and_final_citation_must_identify_the_same_source(self):
        events = self.events()
        checks, blocked = self.evaluate(events)
        self.assertEqual([item['passed'] for item in checks], [True, True])
        self.assertIsNone(blocked)
        events[-1]['payload']['artifact']['metadata']['source_refs'][0]['chunk_id'] = 'other'
        checks, blocked = self.evaluate(events)
        self.assertEqual([item['passed'] for item in checks], [True, False])
        self.assertIsNone(blocked)

    def test_answer_or_another_task_cannot_substitute_for_retrieval(self):
        events = self.events()
        events[2]['task_id'] = 'another-task'
        checks, blocked = self.evaluate(events)
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertEqual(blocked, 'knowledge_retrieval_evidence_unavailable')
        events = self.events()
        events[2]['payload']['artifact']['data']['chunks'][0]['file_id'] = 'other-file'
        checks, blocked = self.evaluate(events)
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertIsNone(blocked)

    def test_truncated_or_missing_result_is_blocked(self):
        events = self.events()
        checks, blocked = self.evaluate(events[:1])
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertEqual(blocked, 'knowledge_retrieval_evidence_unavailable')


class KnowledgeTableEvidenceTests(unittest.TestCase):
    def evaluate(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            return evaluate_knowledge_table(path, 'task', 'query', {'name': 'our_db', 'table_name': 'orders'},
                {'sum_column': 'amount', 'filter_column': 'department', 'filter_value': 'atlas', 'expected_sum': 115})

    def events(self):
        result = {'artifact_id': 'result', 'db_name': 'our_db', 'table_names': ['our_db.orders'],
                  'sql': "SELECT SUM(amount) AS total FROM `our_db`.`orders` WHERE department = 'atlas'",
                  'rows': [[115]], 'columns': ['total']}
        return [
            {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'query_sql', 'call_id': 'call'}},
            {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'query_sql', 'call_id': 'call',
                'success': True, 'output': {'output': result}}},
            {'task_id': 'task', 'kind': 'artifact', 'payload': {'artifact': {'metadata': {
                'matrixflow_type': 'knowledge.answer', 'source_refs': [{'type': 'sql_table', 'database': 'our_db',
                    'table': 'our_db.orders', 'artifact_id': 'result'}]}}}},
        ]

    def test_result_and_citation_must_match_the_case_table(self):
        events = self.events()
        checks, blocked = self.evaluate(events)
        self.assertEqual([item['passed'] for item in checks], [True, True])
        self.assertIsNone(blocked)
        events[-1]['payload']['artifact']['metadata']['source_refs'][0]['artifact_id'] = 'other'
        checks, _ = self.evaluate(events)
        self.assertEqual([item['passed'] for item in checks], [True, False])

    def test_wrong_table_or_result_cannot_pass(self):
        for field, value in (('table_names', ['another.orders']), ('rows', [[116]])):
            events = self.events()
            events[1]['payload']['output']['output'][field] = value
            checks, blocked = self.evaluate(events)
            self.assertFalse(any(item['passed'] for item in checks))
            self.assertIsNone(blocked)

    def test_result_without_a_verifiable_aggregate_is_blocked(self):
        events = self.events()
        events[1]['payload']['output']['output']['sql'] = 'SELECT 115 FROM our_db.orders'
        checks, blocked = self.evaluate(events)
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertEqual(blocked, 'knowledge_sql_evidence_unverifiable')

    def test_grouped_query_checks_the_target_department_and_cited_result(self):
        events = self.events()
        output = events[1]['payload']['output']['output']
        output.update({'sql': 'SELECT department, COUNT(*) AS n, SUM(amount) AS total FROM our_db.orders '
                              'GROUP BY department ORDER BY department',
                       'columns': ['department', 'n', 'total'],
                       'rows': [['atlas', '2', '115'], ['orion', '1', '120']]})
        checks, blocked = self.evaluate(events)
        self.assertEqual([item['passed'] for item in checks], [True, True])
        self.assertIsNone(blocked)
        output['rows'] = [['atlas', '2', '116'], ['orion', '1', '115']]
        checks, blocked = self.evaluate(events)
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertIsNone(blocked)

    def test_unrelated_filter_literal_cannot_validate_a_query(self):
        events = self.events()
        events[1]['payload']['output']['output']['sql'] = (
            "SELECT SUM(amount) AS total FROM our_db.orders WHERE department = 'orion' OR 'atlas' = 'atlas'")
        checks, blocked = self.evaluate(events)
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertEqual(blocked, 'knowledge_sql_evidence_unverifiable')

    def test_truncated_table_result_cannot_validate_a_query(self):
        events = self.events()
        events[1]['payload']['output']['output']['truncated'] = True
        checks, blocked = self.evaluate(events)
        self.assertFalse(any(item['passed'] for item in checks))
        self.assertEqual(blocked, 'knowledge_sql_evidence_unverifiable')


if __name__ == '__main__':
    unittest.main()
