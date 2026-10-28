import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.artifact_checks import check_text_artifact
from journey.catalog import load_case
from journey.client import Failure
from journey.outcomes import assess


class ArtifactChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = self.root / 'artifacts' / 'conversation' / 'inventory.json'
        self.inventory.parent.mkdir(parents=True)
        self.execution = SimpleNamespace(evidence=SimpleNamespace(root=self.root), report={},
                                         collect_artifacts=lambda cid: None, checkpoint=lambda: None)

    def run_check(self, entries, files=None, filename='result.txt'):
        self.inventory.write_text(json.dumps(entries))
        for name, content in (files or {}).items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        checks = []
        delivered = check_text_artifact(self.execution, 'conversation', 'task', filename,
                                        'unique-content', checks, 'artifact')
        return delivered, checks

    def item(self, task='task', name='result.txt', path='download/result.txt'):
        return {'task_id': task, 'file_name': name, 'local_path': path}

    def test_downloaded_content_must_match_the_current_tasks_file(self):
        for data in (b'unique-content', b'unique-content\n', b'unique-content\r\n'):
            delivered, checks = self.run_check([self.item()], {'download/result.txt': data})
            self.assertTrue(delivered)
            self.assertTrue(all(check['passed'] for check in checks))
        delivered, checks = self.run_check([self.item(task='other-task')], {'download/result.txt': b'unique-content'})
        self.assertFalse(delivered)
        self.assertFalse(any(check['passed'] for check in checks))

    def test_wrong_content_reports_a_delivery_with_failed_quality(self):
        delivered, checks = self.run_check([self.item()], {'download/result.txt': b'incorrect'})
        self.assertTrue(delivered)
        self.assertEqual([item['passed'] for item in checks], [True, False])
        result = {'sessions': [{'id': 'artifact', 'artifact_delivered': delivered}],
                  'tasks': [{'phase': 'artifact:deliver', 'state': 'completed'}],
                  'validation': {'checks': [{'passed': True}, *checks]}}
        task, _, delivery, quality = assess(load_case('J2-07'), result, 'failed')
        self.assertEqual(task['state'], 'completed')
        self.assertEqual(delivery['state'], 'delivered')
        self.assertEqual(len(quality['failed_checks']), 1)

    def test_absent_or_ambiguous_files_do_not_count_as_a_delivery(self):
        for items in ([], [self.item(), self.item(path='download/duplicate.txt')]):
            delivered, checks = self.run_check(items)
            self.assertFalse(delivered)
            self.assertFalse(any(check['passed'] for check in checks))

    def test_invalid_filename_and_escaping_download_path_are_rejected(self):
        with self.assertRaisesRegex(Failure, 'invalid_artifact_assertion'):
            self.run_check([], filename='../result.txt')
        with self.assertRaisesRegex(Failure, 'unsafe_artifact_path'):
            self.run_check([self.item(path='../outside')])


if __name__ == '__main__':
    unittest.main()
