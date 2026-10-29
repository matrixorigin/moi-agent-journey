import tempfile
import time
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.catalog import load_case
from journey.client import Failure
from journey.evidence import Evidence
from journey.knowledge_fixture import KnowledgeFixture, KnowledgeFixtureUnavailable, knowledge_selection_ids
from journey.report import finish_case


class FixtureClient:
    def __init__(self, *, referenced=False, create_timeout=False, ingest_failed=False, bad_rows=False,
                 connector_error=False):
        self.deadline = time.monotonic() + 300
        self.referenced, self.create_timeout, self.ingest_failed = referenced, create_timeout, ingest_failed
        self.bad_rows, self.kind, self.connector_error = bad_rows, None, connector_error
        self.calls, self.model, self.reconciled, self.deleted, self.connector_deleted = [], None, False, False, False

    def upload_multipart(self, endpoint, path):
        self.calls.append(('UPLOAD', endpoint))
        self.kind = 'table' if endpoint == '/connectors/file/upload' else 'document'
        return {'conn_file_ids': ['our-file']} if self.kind == 'table' else {'file_id': 'our-file'}

    def product(self, method, path, body=None, *, expected_code=0):
        self.calls.append((method, path))
        assert expected_code == 'OK'
        if path.startswith('/semantic-models?'):
            return {'items': [], 'total': 0}
        if method == 'POST' and path.endswith('/create-with-sources'):
            if self.create_timeout:
                raise Failure('transport_error')
            self.model = {'id': 42, 'name': body['name'], 'tables': [],
                          'files': {'file_ids': None if self.kind == 'table' else ['our-file']}}
            return {'model': self.model, 'data_domain': {'database_id': 8}, 'sources': [{'row_id': 'source'}]}
        if path.endswith('/source-jobs/reconcile'):
            self.reconciled = True
            return {'updated': True}
        if '/sources?' in path:
            status = 'failed' if self.ingest_failed else 'succeeded' if self.reconciled else 'pending'
            source = {'row_id': 'source', 'model_id': 42, 'source_type': 'file' if self.kind == 'document' else 'table',
                      'ingest_status': status, 'error': 'parse failed' if self.ingest_failed else None}
            if self.kind == 'document':
                source['source_file_id'] = 'our-file'
            elif self.reconciled:
                source.update({'db_name': self.model['name'], 'table_name': 'orders', 'kb_table_id': 33})
                self.model['tables'] = [{'db_name': self.model['name'], 'table_names': ['orders']}]
            return {'total': 1, 'items': [source]}
        if path.endswith('/source-jobs'):
            return {'items': [] if self.reconciled else [{'job_id': 'job', 'job_status': 'pending'}],
                    'reconcile_required': not self.reconciled}
        if '/document?' in path:
            return {'segments': [{'content': 'J2-ORION-6B2F5C193D'}]}
        if path == '/catalog/nl2sql/run_sql':
            if 'COUNT(' in body['sql']:
                return {'columns': ['row_count'], 'rows': [[2 if self.bad_rows else '3']]}
            return {'columns': ['total'], 'rows': [[115]]}
        if '/referencing-agents?' in path:
            return {'items': [{'agent_id': 'another-agent'}] if self.referenced else [], 'limit': 100}
        if path == '/semantic-models/42':
            if method == 'DELETE':
                self.deleted = True
                return {'deleted': True}
            if self.deleted:
                raise Failure('http_error', 404)
            return self.model
        raise AssertionError(path)

    def request(self, method, path, body=None, **options):
        self.calls.append((method, path))
        if method == 'POST' and path == '/connectors/file/delete':
            assert body == {'conn_file_id': 'our-file'}
            if self.connector_error:
                raise Failure('transport_error')
            self.connector_deleted = True
            return {'code': 'OK', 'data': None}
        if method == 'GET' and path.startswith('/connectors/file/stream?') and self.connector_deleted:
            raise Failure('http_error', 404)
        raise AssertionError(path)


class KnowledgeFixtureTests(unittest.TestCase):
    def fixture(self, case_id='J2-40', **options):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        client = FixtureClient(**options)
        runner = SimpleNamespace(client=client, case=load_case(case_id), evidence=Evidence(Path(temp.name)),
            report={'run_id': '27e32312-3809-4bb5-9c48-f24a4b75a671', 'harness_errors': []},
            checkpoint=lambda: None)
        return KnowledgeFixture(runner, runner.case['knowledge_fixture']), client, runner

    def test_created_source_is_processed_and_only_its_knowledge_base_is_deleted(self):
        fixture, client, runner = self.fixture()
        with patch('journey.knowledge_fixture.time.sleep'):
            variables = fixture.setup()
        self.assertEqual(variables['knowledge_id'], '42')
        self.assertEqual(fixture.state['source_id'], 'source')
        self.assertEqual(sum(path.endswith('/source-jobs/reconcile') for _, path in client.calls), 1)
        fixture.cleanup()
        self.assertTrue(client.deleted)
        self.assertEqual(fixture.state['cleanup'], 'deleted')
        self.assertEqual(runner.report['harness_errors'], [])
        calls = list(client.calls)
        fixture.cleanup()
        self.assertEqual(client.calls, calls, 'a deliberately retired fixture is not deleted again in finally')

    def test_named_fixtures_have_distinct_identities_and_evidence(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        case = load_case('J2-46')
        runner = SimpleNamespace(client=FixtureClient(), case=case, evidence=Evidence(Path(temp.name)),
            report={'run_id': '27e32312-3809-4bb5-9c48-f24a4b75a671', 'harness_errors': []},
            checkpoint=lambda: None)
        first = KnowledgeFixture(runner, case['knowledge_fixtures']['fern'], alias='fern')
        second = KnowledgeFixture(runner, case['knowledge_fixtures']['birch'], alias='birch')
        self.assertNotEqual(first.state['name'], second.state['name'])
        self.assertEqual(set(runner.report['knowledge_fixtures']), {'fern', 'birch'})
        for alias in ('fern', 'birch'):
            self.assertTrue((Path(temp.name) / 'knowledge-fixtures' / alias / 'knowledge-fixture.json').is_file())
        with self.assertRaisesRegex(Failure, 'invalid_knowledge_fixture_alias'):
            KnowledgeFixture(runner, case['knowledge_fixtures']['fern'], alias='../other')

    def test_selection_requires_distinct_ready_owned_knowledge_bases(self):
        fixtures = {'fern': SimpleNamespace(state={'status': 'ready', 'model_id': 42}),
                    'birch': SimpleNamespace(state={'status': 'ready', 'model_id': 84})}
        self.assertEqual(knowledge_selection_ids(fixtures, ['fern', 'birch']), ['42', '84'])
        for selection in ([], ['missing'], ['fern', 'fern'], 'fern'):
            with self.subTest(selection=selection), self.assertRaisesRegex(Failure, 'invalid_knowledge_selection'):
                knowledge_selection_ids(fixtures, selection)
        fixtures['birch'].state['model_id'] = 42
        with self.assertRaisesRegex(Failure, 'invalid_knowledge_selection'):
            knowledge_selection_ids(fixtures, ['fern', 'birch'])
        fixtures['birch'].state['status'] = 'processing'
        with self.assertRaisesRegex(Failure, 'invalid_knowledge_selection'):
            knowledge_selection_ids(fixtures, ['birch'])

    def test_structured_fixture_checks_actual_rows_and_sum_before_use(self):
        fixture, client, runner = self.fixture('J2-41')
        with patch('journey.knowledge_fixture.time.sleep'):
            variables = fixture.setup()
        self.assertEqual(variables['knowledge_id'], '42')
        self.assertEqual(fixture.state['table_id'], 33)
        self.assertEqual(sum(path == '/catalog/nl2sql/run_sql' for _, path in client.calls), 2)
        fixture.cleanup()
        self.assertTrue(client.deleted)
        self.assertTrue(client.connector_deleted)
        self.assertEqual(fixture.state['upload_cleanup'], 'deleted')
        self.assertEqual(runner.report['harness_errors'], [])

    def test_wrong_imported_row_count_blocks_the_test_and_preserves_cleanup(self):
        fixture, client, runner = self.fixture('J2-41', bad_rows=True)
        with patch('journey.knowledge_fixture.time.sleep'):
            with self.assertRaisesRegex(KnowledgeFixtureUnavailable, 'knowledge_table_fixture_mismatch'):
                fixture.setup()
        fixture.cleanup()
        self.assertTrue(client.deleted)
        self.assertEqual(runner.report['harness_errors'], [])

    def test_referenced_knowledge_base_is_not_deleted(self):
        fixture, client, runner = self.fixture(referenced=True)
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        fixture.cleanup()
        self.assertFalse(client.deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'knowledge_fixture_in_use')

    def test_referenced_table_preserves_both_knowledge_base_and_upload(self):
        fixture, client, runner = self.fixture('J2-41', referenced=True)
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        fixture.cleanup()
        self.assertFalse(client.deleted)
        self.assertFalse(client.connector_deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'knowledge_fixture_in_use')

    def test_unconfirmed_agent_cleanup_preserves_the_knowledge_and_upload(self):
        fixture, client, runner = self.fixture('J2-41')
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        fixture.cleanup(dependencies_confirmed=False)
        self.assertFalse(client.deleted)
        self.assertFalse(client.connector_deleted)
        self.assertEqual(fixture.state['cleanup'], 'retained')
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'dependent_agent_or_task_unconfirmed')

    def test_upload_cleanup_error_is_reported(self):
        fixture, client, runner = self.fixture('J2-41', connector_error=True)
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        fixture.cleanup()
        self.assertTrue(client.deleted)
        self.assertFalse(client.connector_deleted)
        self.assertEqual(fixture.state['cleanup'], 'incomplete')
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'transport_error')

    def test_unknown_create_outcome_is_not_replayed_or_deleted(self):
        fixture, client, runner = self.fixture(create_timeout=True)
        with self.assertRaises(KnowledgeFixtureUnavailable):
            fixture.setup()
        fixture.cleanup()
        self.assertEqual(sum(path.endswith('/create-with-sources') for _, path in client.calls), 1)
        self.assertFalse(client.deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'knowledge_fixture_outcome_unknown')

    def test_unknown_table_create_outcome_preserves_the_upload(self):
        fixture, client, runner = self.fixture('J2-41', create_timeout=True)
        with self.assertRaises(KnowledgeFixtureUnavailable):
            fixture.setup()
        fixture.cleanup()
        self.assertFalse(client.deleted)
        self.assertFalse(client.connector_deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'knowledge_fixture_outcome_unknown')

    def test_failed_ingestion_is_reported_as_unavailable_and_cleanup_is_attempted(self):
        fixture, client, runner = self.fixture(ingest_failed=True)
        with self.assertRaisesRegex(KnowledgeFixtureUnavailable, 'knowledge_source_failed'):
            fixture.setup()
        fixture.cleanup()
        self.assertTrue(client.deleted)
        self.assertEqual(fixture.state['failure']['reason'], 'knowledge_source_failed')
        self.assertEqual(runner.report['harness_errors'], [])

    def test_fixture_unavailable_is_blocked_without_a_framework_failure(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name) / 'J2-40'
        folder.mkdir()
        (folder / 'result.json').write_text(json.dumps({'case_id': 'J2-40', 'workspace': 'journey-2',
            'title': 'knowledge', 'status': 'failed', 'blocked': True, 'capture_complete': False,
            'failures': [{'stage': 'knowledge_fixture', 'reason': 'knowledge_source_failed'}],
            'harness_errors': [], 'tasks': [], 'conversations': [], 'sessions': []}))
        result = finish_case('J2-40', temp.name)
        self.assertEqual(result['functional']['state'], 'blocked')
        self.assertEqual(result['test_status'], 'completed')


if __name__ == '__main__':
    unittest.main()
