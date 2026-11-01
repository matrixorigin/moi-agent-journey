import json
import tempfile
import unittest
from pathlib import Path

from journey.knowledge_checks import evaluate_knowledge, evaluate_knowledge_table


class NativeKnowledgeChecks(unittest.TestCase):
    def run_check(self, refs, *, document=True, baseline=None, task='task', native=True, sql=False):
        metadata = {'matrixflow_type': 'knowledge.answer', 'source_refs': refs}
        if native:
            metadata.update(tool='select_final_sources', cite_then_write=True)
        events = [{'task_id': task, 'kind': 'artifact', 'payload': {'artifact': {'metadata': metadata}}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text('\n'.join(json.dumps(e) for e in events))
            if sql:
                return evaluate_knowledge_table(path, 'task', 'qa', {'name': 'owned', 'table_name': 'orders'},
                    {'sum_column': 'amount', 'filter_column': 'department', 'filter_value': 'atlas',
                     'expected_sum': 115}, baseline=baseline)
            snapshot = {'source': {'model_id': 42, 'row_id': 'source', 'kb_file_id': 'file',
                'effective_enabled': True}, 'segments': [{'chunk_id': 'chunk', 'enabled': True,
                'content': 'unique-marker', 'segment_id': 'chunk'}]}
            if isinstance(document, dict): snapshot = document
            return evaluate_knowledge(path, 'task', 'qa', {'model_id': 42, 'source_id': 'source',
                'file_id': 'file'}, 'unique-marker', document=snapshot if document else None)

    def ref(self):
        return {'type': 'rag_chunk', 'semantic_model_id': 42, 'file_id': 'file',
                'artifact_id': 'server-evidence', 'chunk_ids': ['chunk']}

    def test_native_receipt_resolves_omitted_source_row_against_owned_canonical_snapshot(self):
        checks, blocked = self.run_check([self.ref()])
        self.assertEqual([c['passed'] for c in checks], [True, True])
        self.assertIsNone(blocked)

    def test_native_receipt_cannot_replace_snapshot_or_cross_task_provenance(self):
        checks, blocked = self.run_check([self.ref()], document=False)
        self.assertFalse(any(c['passed'] for c in checks)); self.assertIsNotNone(blocked)
        for options in ({'task': 'other'}, {'native': False}):
            checks, _ = self.run_check([self.ref()], **options)
            self.assertFalse(any(c['passed'] for c in checks))

    def test_wrong_source_file_model_chunk_and_marker_do_not_pass(self):
        for field, value in [('file_id', 'other'), ('semantic_model_id', 43),
                             ('source_row_id', 'other'), ('chunk_ids', ['other'])]:
            ref = self.ref(); ref[field] = value
            checks, _ = self.run_check([ref])
            self.assertFalse(any(c['passed'] for c in checks))
        snapshot = {'source': {'model_id': 42, 'row_id': 'source', 'kb_file_id': 'file',
            'effective_enabled': True}, 'segments': [{'segment_id': 'chunk', 'enabled': True,
            'content': 'unrelated content'}]}
        checks, _ = self.run_check([self.ref()], document=snapshot)
        self.assertFalse(any(c['passed'] for c in checks))

    def sql_ref(self, sql=None):
        return {'type': 'sql_table', 'artifact_id': 'server-sql', 'database': 'owned',
            'table': 'owned.orders', 'sql': sql or "SELECT SUM(amount) AS total FROM orders WHERE department='atlas'"}

    def test_native_sql_receipt_requires_the_verified_fixture_and_exact_query_contract(self):
        baseline = {'columns': ['total'], 'rows': [[115]]}
        for sql in [None, 'SELECT department, COUNT(*) AS n, SUM(amount) AS total FROM owned.orders '
                         'GROUP BY department ORDER BY department']:
            checks, blocked = self.run_check([self.sql_ref(sql)], baseline=baseline, sql=True)
            self.assertTrue(all(c['passed'] for c in checks)); self.assertIsNone(blocked)
        checks, blocked = self.run_check([self.sql_ref()], sql=True)
        self.assertFalse(any(c['passed'] for c in checks)); self.assertIsNotNone(blocked)
        with self.assertRaisesRegex(ValueError, 'snapshot_mismatch'):
            self.run_check([self.sql_ref()], baseline={'columns': ['total'], 'rows': [[116]]}, sql=True)

    def test_native_sql_does_not_accept_constant_results_extra_filters_or_other_tables(self):
        for sql in ["SELECT 115 FROM orders WHERE department='atlas'",
                    "SELECT SUM(amount) FROM orders WHERE department='atlas' AND amount<3",
                    "SELECT SUM(amount) FROM other WHERE department='atlas'",
                    "SELECT SUM(amount) FROM orders WHERE department='ATLAS'",
                    "SELECT SUM(amount) FROM orders WHERE department='atlas' OR 1=1"]:
            checks, _ = self.run_check([self.sql_ref(sql)], baseline={'columns': ['total'], 'rows': [[115]]}, sql=True)
            self.assertFalse(any(c['passed'] for c in checks))
        for options in ({'task': 'other'}, {'native': False}):
            checks, _ = self.run_check([self.sql_ref()], baseline={'columns': ['total'], 'rows': [[115]]}, sql=True, **options)
            self.assertFalse(any(c['passed'] for c in checks))
