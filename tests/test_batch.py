import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from journey.batch import CASE_OVERHEAD_MINUTES, plan_batches, run_batch, run_case, run_stage
from journey.catalog import ROOT, case_executor, load_case, load_suite, plan_suite, step_timeout_minutes


class BatchPlanTests(unittest.TestCase):
    def test_knowledge_suite_is_ten_complete_groups_with_both_evaluation_budgets(self):
        suite = load_suite('journey-3')
        groups = plan_suite(suite)['include']
        self.assertEqual(len(groups), 10)
        self.assertEqual(suite['concurrency'], 10)
        ids = [case_id for group in groups for case_id in group['case_ids']]
        self.assertCountEqual(ids, suite['cases'])
        self.assertEqual(len(ids), len(set(ids)))
        for group in groups:
            self.assertIn(len(group['case_ids']), (5, 6))
            self.assertFalse(group['needs_browser'])
            budget = 15
            for case_id in group['case_ids']:
                case = load_case(case_id)
                self.assertEqual(case['timeout_seconds'], 600)
                self.assertEqual(case['evaluation_timeout_seconds'], 300 if case_id == 'J3-54' else 180)
                budget += step_timeout_minutes(case) + CASE_OVERHEAD_MINUTES
            self.assertEqual(group['job_timeout_minutes'], budget)
            self.assertLessEqual(budget, 360)

    def test_real_suite_is_ten_balanced_groups_with_exactly_once_coverage(self):
        suite = load_suite('journey-2')
        groups = plan_suite(suite)['include']
        self.assertEqual(len(groups), 10)
        self.assertEqual(suite['concurrency'], 10)
        ids = [case_id for group in groups for case_id in group['case_ids']]
        self.assertCountEqual(ids, suite['cases'])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(13 <= len(group['case_ids']) <= 14 for group in groups))
        for group in groups:
            self.assertLessEqual(group['job_timeout_minutes'], 360)
            self.assertEqual(group['needs_browser'], any(
                case_executor(load_case(case_id)) == 'browser' for case_id in group['case_ids']))
        budgets = [group['job_timeout_minutes'] for group in groups]
        self.assertLessEqual(max(budgets) - min(budgets), 9 + CASE_OVERHEAD_MINUTES)
        exceptional = next(group for group in groups if 'J2-111' in group['case_ids'])
        self.assertEqual(exceptional['job_timeout_minutes'],
                         15 + (9 + CASE_OVERHEAD_MINUTES) * len(exceptional['case_ids']) + 10)
        self.assertEqual(plan_suite(load_suite('journey-1'))['include'][0]['case'], 'J1-01')
        self.assertEqual(plan_suite(load_suite('journey-4'))['include'][0]['case'], 'EMPTY')

    def test_invalid_or_oversized_batches_fail_explicitly(self):
        item = {'case': 'J2-01', 'executor': 'api', 'step_timeout_minutes': 9}
        for count in (0, -1, True, '10'):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'invalid_batch_jobs'):
                plan_batches([item], count)
        with self.assertRaisesRegex(ValueError, 'unsupported_batch_executor'):
            plan_batches([{**item, 'executor': 'office'}], 10)
        with self.assertRaisesRegex(ValueError, 'hosted_runner_limit'):
            plan_batches([{**item, 'step_timeout_minutes': 350}], 1)
        self.assertEqual(len(plan_batches([item], 10)), 1)
        self.assertEqual(plan_batches([], 10), [])


class BatchRunnerTests(unittest.TestCase):
    def fake_stages(self, calls, fail=(), timeout=()):
        def stage(command, directory, env, budget):
            directory = Path(directory)
            name = {'journey.catalog': 'prepare', 'journey.functional_case': 'execute',
                    'journey.browser_case': 'execute', 'journey.knowledge_case': 'execute', 'journey.report': 'report',
                    'journey.publish': 'publish'}[command[2]]
            case_id = directory.name
            calls.append((case_id, name, directory, env.copy(), budget, command[2]))
            result_dir = directory / 'results' / case_id
            result_dir.mkdir(parents=True, exist_ok=True)
            if name == 'report':
                (result_dir / 'result.json').write_text(json.dumps({'test_status': 'completed',
                    'functional': {'state': 'fail'}, 'task_seconds': 12}))
                (result_dir / 'case-summary.md').write_text('## ' + case_id + ' report\n')
            if name == 'publish':
                public = directory / 'published-results' / case_id
                public.mkdir(parents=True)
                (public / 'result.json').write_text('{}')
            return {'exit_code': 1 if name in fail else 0, 'timed_out': name in timeout}
        return stage

    def run_single(self, root, calls, case_id='J2-02', **kwargs):
        with patch('journey.batch.run_stage', side_effect=self.fake_stages(calls, **kwargs)):
            return run_case(load_case(case_id), load_case(case_id)['workspace'], root / case_id, root / 'public', ROOT,
                            {'MOI_UI_PASSWORD': 'private-ui-password'})

    def test_failure_or_timeout_still_reports_and_publishes(self):
        for settings in ({'fail': {'execute'}}, {'timeout': {'execute'}}, {'fail': {'prepare'}}):
            with self.subTest(settings=settings), tempfile.TemporaryDirectory() as tmp:
                calls = []
                row = self.run_single(Path(tmp).resolve(), calls, **settings)
                self.assertEqual([call[1] for call in calls][-2:], ['report', 'publish'])
                self.assertEqual(row['failed_stages'], list(settings.get('fail', settings.get('timeout'))))
                if 'prepare' in settings.get('fail', ()):
                    self.assertNotIn('execute', [call[1] for call in calls])
                self.assertTrue(all('MOI_UI_PASSWORD' not in call[3] for call in calls))

    def test_later_cases_run_in_isolated_directories_after_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, calls = Path(tmp).resolve(), []
            env = {'GITHUB_STEP_SUMMARY': str(root / 'summary.md')}
            with patch('journey.batch.run_stage', side_effect=self.fake_stages(calls, fail={'execute'})):
                self.assertEqual(run_batch(['J2-02', 'J2-06'], 'journey-2', root / 'work', root / 'public', env), 1)
            self.assertEqual([case_id for case_id, name, *_ in calls if name == 'execute'], ['J2-02', 'J2-06'])
            self.assertNotEqual(calls[0][2], calls[-1][2])
            rows = json.loads((root / 'work/batch-result.json').read_text())['cases']
            self.assertEqual(len(rows), 2)
            for case_id in ('J2-02', 'J2-06'):
                self.assertIn(case_id + ' 单 case 报告', (root / 'summary.md').read_text())
                self.assertTrue((root / 'public' / case_id / 'result.json').is_file())

    def test_publication_failure_is_not_hidden_and_later_cases_still_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, calls = Path(tmp).resolve(), []
            with patch('journey.batch.run_stage', side_effect=self.fake_stages(calls, fail={'publish'})):
                self.assertEqual(run_batch(['J2-02', 'J2-06'], 'journey-2', root / 'work', root / 'public', {}), 1)
            rows = json.loads((root / 'work/batch-result.json').read_text())['cases']
            self.assertEqual([row['failed_stages'] for row in rows], [['publish'], ['publish']])
            self.assertFalse((root / 'public').exists())

    def test_product_failure_does_not_turn_group_red(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.batch.run_stage',
                side_effect=self.fake_stages([])):
            root = Path(tmp).resolve()
            self.assertEqual(run_batch(['J2-02'], 'journey-2', root / 'work', root / 'public', {}), 0)

    def test_browser_credentials_and_exceptional_execution_budget_are_preserved(self):
        for case_id, budget, browser in (('J2-01', 540, True), ('J2-111', 1140, False),
                                         ('J3-01', 1200, False), ('J3-54', 1440, False)):
            with self.subTest(case_id=case_id), tempfile.TemporaryDirectory() as tmp:
                calls = []
                self.run_single(Path(tmp).resolve(), calls, case_id)
                execute = next(call for call in calls if call[1] == 'execute')
                self.assertEqual(execute[4], budget)
                self.assertEqual('MOI_UI_PASSWORD' in execute[3], browser)
                self.assertEqual(execute[5], 'journey.knowledge_case' if case_id.startswith('J3-') else
                                 'journey.browser_case' if browser else 'journey.functional_case')

    def test_unexpected_error_continues_but_cancellation_stops(self):
        for error, count in ((OSError('synthetic-error'), 2), (KeyboardInterrupt(), 1)):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                success = {'case_id': 'J2-06', 'directory': str(root / 'J2-06'), 'failed_stages': []}
                with patch('journey.batch.run_case', side_effect=[error, success]) as run:
                    if isinstance(error, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            run_batch(['J2-02', 'J2-06'], 'journey-2', root, root / 'public', {})
                    else:
                        self.assertEqual(run_batch(['J2-02', 'J2-06'], 'journey-2', root, root / 'public', {}), 1)
                    self.assertEqual(run.call_count, count)

    def test_invalid_case_selection_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.batch.run_case') as run:
            root = Path(tmp).resolve()
            for ids in ([], ['J2-02', 'J2-02'], ['J1-01'], [{}], 'J2-02'):
                with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, 'invalid_batch_case_ids'):
                    run_batch(ids, 'journey-2', root, root / 'public', {})
            run.assert_not_called()

    def test_existing_public_results_cannot_be_reused_as_new_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.batch.run_case') as run:
            root = Path(tmp).resolve()
            (root / 'public/J2-02').mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, 'batch_publication_already_exists'):
                run_batch(['J2-02'], 'journey-2', root / 'work', root / 'public', {})
            run.assert_not_called()

    def test_stage_timeout_is_a_framework_error_in_the_individual_report(self):
        from journey.report import finish_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            case_dir = root / 'J2-02'
            case_dir.mkdir()
            (case_dir / 'result.json').write_text(json.dumps({'status': 'failed', 'tasks': [],
                'failures': [], 'harness_errors': []}))
            (case_dir / 'batch-stages.json').write_text(json.dumps({'case_id': 'J2-02',
                'stages': {'execute': {'exit_code': -9, 'timed_out': True}}}))
            result = finish_case('J2-02', root)
            self.assertEqual(result['test_status'], 'error')
            self.assertEqual(result['harness_errors'][0]['reason'], 'process_timeout')
            self.assertEqual(result['harness_errors'][0]['stage'], 'batch:execute')

    def test_real_stage_timeout_stops_the_child_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            pid_file = root / 'pid'
            script = f'import os,time; open({str(pid_file)!r}, "w").write(str(os.getpid())); time.sleep(20)'
            result = run_stage([sys.executable, '-c', script], root, os.environ.copy(), 0.5)
            self.assertTrue(result['timed_out'])
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)

    def test_timeout_stops_a_detached_browser_worker_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            child_pid = root / 'worker-pid'
            worker = f'import os,time; open({str(child_pid)!r}, "w").write(str(os.getpid())); time.sleep(20)'
            script = ('import signal,subprocess,sys,time\n'
                      f'p=subprocess.Popen([sys.executable,"-c",{worker!r}],start_new_session=True)\n'
                      'def stop(*_):\n p.wait(); sys.exit(0)\n'
                      'signal.signal(signal.SIGTERM,stop)\n'
                      'time.sleep(20)\n')
            result = run_stage([sys.executable, '-c', script], root, os.environ.copy(), 0.5)
            self.assertTrue(result['timed_out'])
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)

    def test_real_pipeline_publishes_two_reports_without_qa_credentials(self):
        for workspace, ids in [('journey-2', ['J2-02', 'J2-06']), ('journey-3', ['J3-01', 'J3-02'])]:
            with self.subTest(workspace=workspace), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                env = {key: value for key, value in os.environ.items() if not key.startswith('MOI_')}
                self.assertEqual(run_batch(ids, workspace, root / 'work', root / 'public', env), 1)
                rows = json.loads((root / 'work/batch-result.json').read_text())['cases']
                self.assertEqual([row['case_id'] for row in rows], ids)
                self.assertTrue(all(row['failed_stages'] == ['execute', 'report'] for row in rows))
                for case_id in ids:
                    public = root / 'public' / case_id
                    result = json.loads((public / 'result.json').read_text())
                    self.assertEqual(result['test_status'], 'error')
                    self.assertEqual(result['harness_errors'][0]['stage'], 'configuration')
                    self.assertTrue((public / 'case-summary.md').is_file())
                    self.assertEqual(json.loads((public / 'publication.json').read_text())['status'], 'complete')
                    if workspace == 'journey-3':
                        self.assertEqual(result['rag']['expected_answers'], 1)


if __name__ == '__main__':
    unittest.main()
