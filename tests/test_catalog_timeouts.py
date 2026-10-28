import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.catalog import load_case, main
from journey.client import Failure
from journey.evidence import Evidence
from journey.run_case import Execution


class CaseTimeoutTests(unittest.TestCase):
    def plan(self, workspace):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['catalog', 'plan', workspace]), \
                patch.dict(os.environ, {'GITHUB_OUTPUT': ''}), contextlib.redirect_stdout(output):
            main()
        return json.loads(output.getvalue())['include']

    def test_each_journey_keeps_its_own_execution_budget(self):
        self.assertEqual(load_case('J1-01')['timeout_seconds'], 3600)
        self.assertEqual(load_case('J2-01')['timeout_seconds'], 300)
        self.assertTrue(all(item['step_timeout_minutes'] == 64 for item in self.plan('journey-1')))
        self.assertTrue(all(item['step_timeout_minutes'] == 9 for item in self.plan('journey-2')))
        self.assertEqual(self.plan('journey-3')[0]['step_timeout_minutes'], 1)

    def test_explicit_case_exception_is_reflected_in_the_workflow_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_dir = root / 'cases' / 'journey-2' / 'J2-100'
            case_dir.mkdir(parents=True)
            case_path = case_dir / 'case.json'
            case = {'id': 'J2-100', 'workspace': 'journey-2', 'timeout_seconds': 601}
            case_path.write_text(json.dumps(case))
            config = {'case_timeout_seconds': 3600, 'model': 'test', 'journeys': [
                {'workspace': 'journey-2', 'executor': 'api', 'concurrency': 3,
                 'case_timeout_seconds': 300, 'cases': ['J2-100']}]}
            (root / 'suites.json').write_text(json.dumps(config))
            with patch('journey.catalog.ROOT', root):
                self.assertEqual(load_case('J2-100')['timeout_seconds'], 601)
                self.assertEqual(self.plan('journey-2')[0]['step_timeout_minutes'], 15)
                for invalid in (0, -1, True, '300'):
                    case['timeout_seconds'] = invalid
                    case_path.write_text(json.dumps(case))
                    with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'invalid_case_timeout'):
                        load_case('J2-100')

    def test_timeout_before_submission_is_a_framework_error(self):
        with tempfile.TemporaryDirectory() as directory:
            case = {'id': 'J2-02', 'title': 'image', 'workspace': 'journey-2', 'timeout_seconds': 300}
            execution = Execution(SimpleNamespace(requests=0), case, Evidence(Path(directory)), directory, 'model')
            execution.record_failure(Failure('case_timeout'))
            self.assertEqual(execution.report['status'], 'error')
            self.assertEqual(execution.report['harness_errors'][0]['reason'], 'case_timeout')
            execution.tasks.append({'task_id': 'started'})
            execution.record_failure(Failure('case_timeout'))
            self.assertEqual(execution.report['status'], 'timeout')
            self.assertEqual(execution.report['failures'][0]['reason'], 'case_timeout')

    def test_real_deadline_interrupts_discovery_and_writes_a_report(self):
        script = '''
import os, sys, time
from unittest.mock import patch
from journey.functional_case import main
case = {"id": "J2-02", "title": "image", "workspace": "journey-2",
        "timeout_seconds": 1, "model": "test", "sessions": []}
os.environ.update(MOI_BASE_URL="https://example.invalid", MOI_PAT="synthetic-pat")
sys.argv = ["functional_case", "J2-02"]
with patch("journey.functional_case.load_case", return_value=case), \\
     patch("journey.functional_case.discover", side_effect=lambda *_: time.sleep(10)):
    raise SystemExit(main())
'''
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
            result = subprocess.run([sys.executable, '-c', script], cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads((Path(directory) / 'results/J2-02/result.json').read_text())
            self.assertEqual(report['status'], 'error')
            self.assertEqual(report['stage'], 'discovery')
            self.assertEqual(report['harness_errors'][0]['reason'], 'case_timeout')
            self.assertEqual(report['tasks'], [])


if __name__ == '__main__':
    unittest.main()
