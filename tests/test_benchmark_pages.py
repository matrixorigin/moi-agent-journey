import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from journey.benchmark_history import metrics_row
from journey.benchmark_pages import build_site, write_snapshot


class BenchmarkPagesTests(unittest.TestCase):
    def test_latest_details_are_filtered_and_credentials_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            path = state / 'production.json'
            base = metrics_row(['J3-01', '2026-11-18 00:00:00', 12.5, 40, 4, 3, 'completed',
                                'rev1', 'snap1', 'judge1', 'a' * 32, 'eligible', '44', 2,
                                'completed', 3, 3, 1])
            rows = [base, {**base, 'case_id': 'J3-02', 'qa_outcome': 'timeout'},
                    {**base, 'case_id': 'J3-03', 'qa_outcome': 'error'},
                    {**base, 'case_id': 'J3-04', 'run_attempt': 1}]
            summary = {'run_id': '44', 'attempt': 2, 'report_error_cases': 1,
                       'coverage': {'recorded': 3, 'planned': 3, 'missing': []}}
            details = {
                'errors': [['J3-03', 's3', 't3', 'bad request'], ['J3-02', 's2', 't2', 'timed out'],
                           ['J3-01', 's1', 't1', 'grading error']],
                'timeouts': [['J3-02', 's2', 't2', 12.5]],
                'tools': [['J3-01', 's1', 't1', 'sql', 'SELECT 1', 'request failed secret-fixture']],
                'scores': [['J3-01', 's1', 't1', 40, ['wrong total'], 'unexpected column'],
                           ['J3-04', 's4', 't4', 40, ['old attempt']]],
                'question': 'unexpected field',
            }
            with patch.dict('os.environ', {'MOI_PAT': 'secret-fixture'}):
                self.assertTrue(write_snapshot(path, 'production', rows, summary, {'44': {'number': 102}}, details=details))
            current = json.loads(path.read_text())['current']
            self.assertEqual(current['errors'], details['errors'][:1])
            self.assertEqual(current['timeouts'], details['timeouts'])
            self.assertEqual(current['tools'][0][-1], 'request failed [REDACTED]')
            self.assertEqual(current['scores'], [details['scores'][0][:5]])
            self.assertNotIn('question', current)
            build_site(state, state / 'site')
            self.assertIn('"metrics_only": false', (state / 'site/production/index.html').read_text())

    def test_publish_both_environments_and_keep_newer_state_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'state'
            state.mkdir()
            output = root / 'pages'
            incoming = root / 'snapshot.json'

            def stage(environment, number, attempt=1):
                run_id = str(number + 1000)
                row = metrics_row(['J3-01', '2026-11-18 00:00:00', 12.5, 75, 4, 3, 'completed',
                                   'rev1', 'snap1', 'judge1', 'a'*32, 'eligible', run_id, attempt,
                                   'completed', 3, 3, 1])
                row['private_tool_output'] = 'PRIVATE ROW'
                summary = {'run_id': run_id, 'attempt': attempt, 'report_error_cases': 1,
                           'coverage': {'recorded': 1, 'planned': 2, 'missing': ['J3-02']},
                           'errors': [['J3-01', 'PRIVATE TRACE', 'PRIVATE ERROR']]}
                write_snapshot(incoming, environment, [row], summary,
                               {run_id: {'number': number, 'private': 'PRIVATE METADATA'}})
                build_site(state, output, incoming=incoming, environment=environment)

            stage('qa', 101)
            pending = (output / 'production/index.html').read_text()
            self.assertIn('const pagesEnvironment = "Production"', pending)
            self.assertIn('const currentRun = null;', pending)

            stage('production', 102)
            production = (state / 'production.json').read_bytes()
            stage('qa', 103)
            stage('qa', 101, attempt=2)
            stage('qa', 103, attempt=2)
            self.assertEqual((state / 'production.json').read_bytes(), production)
            self.assertEqual(json.loads((state / 'qa.json').read_bytes())['current']['attempt'], 2)
            self.assertEqual(json.loads((state / 'qa.json').read_bytes())['current']['run_id'], '1103')

            for key, label in [('qa', 'QA'), ('production', 'Production')]:
                report = (output / key / 'index.html').read_text()
                self.assertIn(f'const pagesEnvironment = "{label}"', report)
                self.assertIn('"metrics_only": true', report)
                self.assertIn('"missing": ["J3-02"]', report)
                self.assertNotIn('PRIVATE', report)
            build_site(state, output)
            self.assertIn('href="qa/"', (output / 'index.html').read_text())

    def test_skip_empty_snapshot_and_reject_environment_mix_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            incoming = root / 'snapshot.json'
            self.assertFalse(write_snapshot(incoming, 'qa', [], {'coverage': {'recorded': 0}}, {}))
            self.assertFalse(incoming.exists())
            write_snapshot(incoming, 'qa', [], {
                'run_id': '44', 'attempt': 1, 'report_error_cases': 0,
                'coverage': {'recorded': 1, 'planned': 1, 'missing': []},
            }, {'44': {'number': 97}})
            with self.assertRaisesRegex(ValueError, 'wrong environment'):
                build_site(root, root / 'pages', incoming=incoming, environment='production')
            self.assertFalse((root / 'production.json').exists())
