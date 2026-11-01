import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.catalog import load_case
from journey.evidence import Evidence
from journey.functional_case import FunctionalExecution
from journey.report import finish_case


class EarlyFailureTests(unittest.TestCase):
    def test_missing_pdf_retains_failed_delivery_check_and_reports_functional_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = Evidence(root / 'J2-128')
            client = SimpleNamespace(requests=0, deadline=time.monotonic()+300, observer=None)
            execution = FunctionalExecution(client, load_case('J2-128'), evidence, directory, 'model')
            entry = {'task_id': 'task', 'conversation_id': 'conversation', 'phase': 'generate:file',
                     'state': 'completed', 'event_count': 0, 'capture_complete': True}
            task = {'status': {'state': 'completed', 'message': {'role': 'agent',
                    'parts': [{'kind': 'text', 'text': 'Upload failed; no artifact delivered.'}]}}}
            def send(*args, **kwargs):
                execution.tasks.append(entry)
                return entry, task
            def finalize():
                execution.report.update(cleanup_seconds=0, capture_complete=True)
            with patch('journey.functional_case.discover', return_value=('/workspaces/workspace', {})), \
                 patch('journey.functional_case.start_version'), patch('journey.functional_case.finish_version'), \
                 patch.object(execution, 'conversation', return_value='conversation'), \
                 patch.object(execution, 'send', side_effect=send), \
                 patch.object(execution, 'collect_artifacts', side_effect=lambda cid:
                    evidence.write('artifacts/'+cid+'/inventory.json', [])), \
                 patch.object(execution, 'finalize', side_effect=finalize):
                result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertTrue(any(not check['passed'] and check['rule']=='交付指定文件'
                                for check in result['validation']['checks']))
            evidence.write('result.json', result)
            finished = finish_case('J2-128', root)
            self.assertEqual(finished['functional']['state'], 'fail')
            self.assertEqual(finished['test_status'], 'completed')
            self.assertFalse(finished['harness_errors'])
