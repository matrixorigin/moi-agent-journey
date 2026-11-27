import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from journey.moi_report.__main__ import main
from journey.moi_report.publish import Publication, literal, quoted
from journey.moi_report.tables import from_comparison, validate_payload
from test_j2_score_runs import write_run
from journey.j2_scoring.compare import build_comparison


class FakeClient:
    def __init__(self):
        self.databases = []
        self.tables = {}
        self.dashboards = {}
        self.next_id = 100
        self.calls = []
        self.fail_next_insert = False

    def product(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/catalog/list':
            return {'list': [{'name': 'default', 'id': 'catalog'}]}
        if path == '/catalog/database/list':
            return {'list': self.databases}
        if path == '/catalog/database/create':
            item = {'id': self.new_id(), 'name': body['name']}
            self.databases.append(item)
            return item
        if path == '/catalog/table/create':
            item = {'id': self.new_id(), 'name': body['name'], 'type': 'table',
                    'columns': body['columns'], 'rows': []}
            self.tables[item['name']] = item
            return item
        if path == '/catalog/database/children':
            return {'list': list(self.tables.values())}
        if path == '/query/run':
            if self.fail_next_insert:
                self.fail_next_insert = False
                return {'status': 'failed'}
            name = next(name for name, t in self.tables.items() if '`' + name + '`' in body['query'])
            table = self.tables[name]
            assert body['query'].count('INSERT INTO') == 1
            self.tables[name]['rows'].extend(self._pending_rows)
            return {'status': 'success'}
        if path == '/catalog/nl2sql/run_sql':
            name = next(name for name, t in self.tables.items() if '`' + name + '`' in body['sql'])
            table = self.tables[name]
            fields = [c['name'] for c in table['columns']]
            rows = [[self.format(row.get(field)) for field in fields] + [self.null(row.get(field)) for field in fields]
                    for row in table['rows']]
            return {'rows': rows}
        if path == '/data-dashboards' and method == 'POST':
            self.next_id += 1
            item = {'ID': 'dashboard-' + str(self.next_id), 'charts': []}
            self.dashboards[item['ID']] = item
            return item
        if path.startswith('/data-dashboards/') and path.endswith('/result'):
            chart_id = path.split('/')[-2]
            chart = next(c for d in self.dashboards.values() for c in d['charts'] if c['ID'] == chart_id)
            return {'status': 'completed', 'result': {'total': chart['rows']}}
        if path.startswith('/data-dashboards/') and path.endswith('/charts') and method == 'POST':
            item = dict(body, ID='chart-' + str(len(self.dashboards[path.split('/')[2]]['charts'])), rows=self._chart_rows)
            self.dashboards[path.split('/')[2]]['charts'].append(item)
            return item
        if path.startswith('/data-dashboards/') and method == 'GET':
            return self.dashboards[path.rsplit('/', 1)[-1]]
        raise AssertionError(path)

    def new_id(self):
        self.next_id += 1
        return self.next_id

    @staticmethod
    def null(value):
        return 1 if value is None else 0

    @staticmethod
    def format(value):
        return None if value is None else str(value)


class MoiReportTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(Path('tests/fixtures/moi-report/dashboard-tables.json').read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name) / 'ledger.json'

    def publish(self, client=None, resume=False):
        client = client or FakeClient()
        publication = Publication(client, self.payload, database_name='synthetic_report', title='Synthetic report',
                                 description='Offline synthetic test', state_path=self.ledger)
        self._pending_rows = {name: rows[:] for name, rows in self.payload['tables'].items()}
        client._pending_rows = []
        client._chart_rows = 2
        original = client.product
        def product(method, path, body=None):
            if path == '/query/run':
                name = next(name for name, t in client.tables.items() if '`' + name + '`' in body['query'])
                client._pending_rows = self._pending_rows[name][:80]
            if path.endswith('/charts') and method == 'POST':
                name = body['name']
                metric = name.startswith(('评测用例', '已评分', '同口径可比', '满分', '未满分', '框架', '100秒内', '200秒内', '评分覆盖'))
                threshold = name == '100秒/200秒完成情况对照'
                if name.startswith('低分及未评分归因分类'):
                    client._chart_rows = 2
                    return original(method, path, body)
                client._chart_rows = 1 if metric or name.startswith('SQL路径评分') else 2
            return original(method, path, body)
        client.product = product
        state = publication.publish()
        return client, state

    def test_complete_publication_uses_27_versioned_charts_and_verifies_values(self):
        client, state = self.publish()
        self.assertEqual(state['stage'], 'completed')
        self.assertEqual(len(state['charts']), 20)
        self.assertEqual(sum(len(t['rows']) for t in client.tables.values()), 22)
        self.assertEqual(len(state['chart_results']), 20)
        self.assertTrue(all(item['verified'] for item in state['tables'].values()))

    def test_sql_filter_and_expected_rows_must_use_actual_threshold_values(self):
        from journey.moi_report.config import specs
        chart = next(s for s in specs(lambda name: '`report`.`' + name + '`')
                     if s['name'] == '100秒/200秒完成情况对照')
        self.assertIn('IN (100,200)', chart['sql'])
        rows = [row for row in self.payload['tables']['duration_curve']
                if row['threshold_seconds'] in (100, 200)]
        self.assertEqual(len(rows), 2)
        self.assertEqual(chart['expected_rows'], 2)

    def test_resume_does_not_duplicate_created_resources_or_rows(self):
        client, first = self.publish()
        state = json.loads(self.ledger.read_text())
        state['stage'] = 'paused'
        self.ledger.write_text(json.dumps(state))
        resumed = Publication(client, self.payload, database_name='synthetic_report', title='ignored',
                             description='ignored', state_path=self.ledger).publish()
        self.assertEqual(resumed['stage'], 'completed')
        self.assertEqual(len(client.databases), 1)
        self.assertEqual(sum(len(t['rows']) for t in client.tables.values()), 22)

    def test_changed_scope_or_table_schema_cannot_resume_old_identity(self):
        client, _ = self.publish()
        state = json.loads(self.ledger.read_text())
        state['stage'] = 'paused'
        state['tables']['case_results']['verified'] = False
        self.ledger.write_text(json.dumps(state))
        payload = copy.deepcopy(self.payload)
        payload['tables']['case_results'][0]['title'] = 'changed'
        payload['tables']['case_results'] = payload['tables']['case_results'][:1]
        with self.assertRaisesRegex(ValueError, 'report_table_schema_changed_use_new_database'):
            Publication(client, payload, database_name='synthetic_report', title='ignored',
                        description='ignored', state_path=self.ledger).publish()
        payload = copy.deepcopy(self.payload)
        payload['scope']['suite'] = 'different'
        with self.assertRaisesRegex(ValueError, 'resume_scope_mismatch'):
            Publication(client, payload, database_name='synthetic_report', title='ignored',
                        description='ignored', state_path=self.ledger).publish()

    def test_unknown_insert_failure_is_recorded_before_result_is_known(self):
        client = FakeClient()
        client.fail_next_insert = True
        with self.assertRaisesRegex(ValueError, 'insert_not_successful'):
            self.publish(client)
        state = json.loads(self.ledger.read_text())
        self.assertTrue(state['stage'].startswith('received:insert-'))

    def test_client_side_rules_preserve_null_and_reject_injection(self):
        self.assertEqual(literal(None), 'NULL')
        self.assertEqual(literal("a'b\\c"), "'a''b\\\\c'")
        for value in ('bad name', 'x`;DROP TABLE t;--', ''):
            with self.assertRaises(ValueError):
                quoted(value)
        for value in (True, {'x': 1}, ['x']):
            with self.assertRaises(ValueError):
                literal(value)

    def test_projection_retains_case_scores_coverage_and_source_hashes(self):
        root = Path(self.temp.name)
        write_run(root / 'old')
        write_run(root / 'new', partial=True)
        suite = root / 'suite.json'
        suite.write_text(json.dumps({'suite_id': 'synthetic', 'cases': [
            {'id': 'J2-13', 'domain': 'tools'}, {'id': 'J2-37', 'domain': 'feedback'}]}))
        comparison = build_comparison(suite, root / 'old', root / 'new')
        payload = from_comparison(comparison)
        self.assertEqual(payload['tables']['case_results'][0]['current_bps'], 5000)
        self.assertEqual(payload['tables']['case_results'][0]['delta_bps'], -5000)
        self.assertEqual(payload['tables']['overview'][0]['scored_cases'], 2)
        self.assertEqual(len(payload['tables']['failed_checks']), 2)
        self.assertNotIn('overall_moi_score', payload)

    def test_table_contract_requires_complete_consistent_columns(self):
        value = copy.deepcopy(self.payload)
        value['tables']['case_results'][0]['extra'] = 'not allowed'
        with self.assertRaisesRegex(ValueError, 'column_mismatch'):
            validate_payload(value)

    def test_prepare_is_offline_and_refuses_overwrite(self):
        source = json.loads(Path('tests/fixtures/moi-report/comparison.json').read_text())
        comparison = Path('tests/fixtures/moi-report/comparison.json')
        comparison.write_text(json.dumps(source))
        output = Path(self.temp.name) / 'prepared'
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            self.assertEqual(main(['prepare', '--comparison', str(comparison), '--output', str(output)]), 0)
            with self.assertRaises(FileExistsError):
                main(['prepare', '--comparison', str(comparison), '--output', str(output)])
