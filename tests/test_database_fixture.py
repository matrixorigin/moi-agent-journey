import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.catalog import load_case
from journey.client import Failure
from journey.database_fixture import DatabaseFixture, DatabaseFixtureUnavailable
from journey.evidence import Evidence


class DatabaseClient:
    def __init__(self, *, referenced=False, table_referenced=False, wrong_identity=False,
                 insert_timeout=False, wrong_data=False):
        self.deadline = time.monotonic() + 300
        self.referenced, self.wrong_identity = referenced, wrong_identity
        self.table_referenced = table_referenced
        self.insert_timeout, self.wrong_data = insert_timeout, wrong_data
        self.name, self.table_created, self.table_deleted, self.database_deleted = None, False, False, False
        self.calls = []

    def product(self, method, path, body=None, *, expected_code=0):
        self.calls.append((method, path, body))
        assert method == 'POST'
        if path == '/catalog/list':
            return {'list': [{'id': 1, 'name': 'default', 'reserved': True}]}
        if path == '/catalog/database/list':
            entries = [] if not self.name or self.database_deleted else [{'id': 42, 'name': self.name, 'reserved': False}]
            return {'list': entries}
        if path == '/catalog/database/create':
            self.name = body['name']
            return {'id': 42}
        if path == '/catalog/database/info':
            return {'database': {'id': 42, 'name': 'someone_else' if self.wrong_identity else self.name}}
        if path == '/catalog/table/create':
            self.table_created = True
            return {'id': 99}
        if path == '/catalog/database/children':
            return {'list': ([{'id': 99, 'name': 'orders', 'type': 'table'}]
                             if self.table_created and not self.table_deleted else [])}
        if path in {'/catalog/database/ref_list', '/catalog/table/ref_list'}:
            referenced = self.referenced or (path == '/catalog/table/ref_list' and self.table_referenced)
            return {'list': [{'ref_id': 'foreign'}] if referenced else []}
        if path == '/query/run':
            assert expected_code == 200
            if self.insert_timeout:
                raise Failure('transport_error')
            return {'status': 'success', 'rows_affected': 3}
        if path == '/catalog/nl2sql/run_sql':
            rows = [['orion', '120'], ['atlas', '35'], ['atlas', '80']]
            return {'columns': ['department', 'amount'], 'rows': rows[:-1] if self.wrong_data else rows}
        raise AssertionError(path)

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/catalog/table/delete':
            assert body == {'id': 99}
            self.table_deleted = True
            return {'code': 'OK', 'data': None}
        if path == '/catalog/database/delete':
            assert body == {'id': 42}
            self.database_deleted = True
            return {'code': 'OK', 'data': None}
        raise AssertionError(path)


class DatabaseFixtureTests(unittest.TestCase):
    def fixture(self, **options):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        client = DatabaseClient(**options)
        runner = SimpleNamespace(client=client, case=load_case('J2-43'), evidence=Evidence(Path(temp.name)),
            report={'run_id': '27e32312-3809-4bb5-9c48-f24a4b75a671', 'harness_errors': []},
            checkpoint=lambda: None)
        return DatabaseFixture(runner, runner.case['database_fixture']), client, runner

    def test_real_rows_are_verified_and_only_owned_resources_are_removed(self):
        fixture, client, runner = self.fixture()
        values = fixture.setup()
        self.assertEqual(values['database_id'], '42')
        self.assertEqual(values['table_id'], '99')
        self.assertTrue(values['database_name'].startswith('journey2_db_'))
        fixture.cleanup()
        self.assertTrue(client.table_deleted)
        self.assertTrue(client.database_deleted)
        self.assertEqual(fixture.state['cleanup'], 'deleted')
        self.assertEqual(runner.report['harness_errors'], [])

    def test_incorrect_import_blocks_the_case_and_cleans_owned_data(self):
        fixture, client, runner = self.fixture(wrong_data=True)
        with self.assertRaisesRegex(DatabaseFixtureUnavailable, 'database_fixture_data_mismatch'):
            fixture.setup()
        fixture.cleanup()
        self.assertTrue(client.database_deleted)
        self.assertEqual(runner.report['harness_errors'], [])

    def test_identity_mismatch_prevents_writes_and_deletion(self):
        fixture, client, runner = self.fixture(wrong_identity=True)
        with self.assertRaisesRegex(DatabaseFixtureUnavailable, 'database_identity_mismatch'):
            fixture.setup()
        fixture.cleanup()
        self.assertFalse(client.table_created)
        self.assertFalse(client.database_deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'database_identity_mismatch')

    def test_unknown_write_outcome_is_neither_retried_nor_deleted(self):
        fixture, client, runner = self.fixture(insert_timeout=True)
        with self.assertRaisesRegex(DatabaseFixtureUnavailable, 'transport_error'):
            fixture.setup()
        fixture.cleanup()
        self.assertEqual(sum(path == '/query/run' for _, path, _ in client.calls), 1)
        self.assertFalse(client.table_deleted)
        self.assertFalse(client.database_deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'database_mutation_outcome_unknown')

    def test_references_prevent_deleting_any_resources(self):
        fixture, client, runner = self.fixture(referenced=True)
        fixture.setup()
        fixture.cleanup()
        self.assertFalse(client.table_deleted)
        self.assertFalse(client.database_deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'database_fixture_in_use')

    def test_unconfirmed_task_retains_the_database(self):
        fixture, client, runner = self.fixture()
        fixture.setup()
        fixture.cleanup(dependencies_confirmed=False)
        self.assertFalse(client.table_deleted)
        self.assertFalse(client.database_deleted)
        self.assertEqual(fixture.state['cleanup'], 'retained')
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'dependent_task_unconfirmed')

    def test_table_reference_prevents_deleting_the_table_and_database(self):
        fixture, client, runner = self.fixture(table_referenced=True)
        fixture.setup()
        fixture.cleanup()
        self.assertFalse(client.table_deleted)
        self.assertFalse(client.database_deleted)
        self.assertEqual(runner.report['harness_errors'][0]['reason'], 'database_fixture_in_use')

    def test_invalid_sql_values_are_rejected_before_any_mutation(self):
        fixture, client, runner = self.fixture()
        bad = dict(runner.case['database_fixture'])
        bad['rows'] = [["atlas'); DROP DATABASE x; --", 80]]
        with self.assertRaisesRegex(Failure, 'invalid_database_fixture_row'):
            DatabaseFixture(runner, bad)
        self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main()
