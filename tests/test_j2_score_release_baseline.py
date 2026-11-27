import json
from pathlib import Path
import tempfile
import unittest

from journey.j2_scoring.release_baseline import select_previous_release


class ReleaseBaselineTests(unittest.TestCase):
    def run_fixture(self, root, name, version, date, *, score='100.00', complete=True, end=None):
        p = root / name
        (p / 'results/J2-03').mkdir(parents=True)
        manifest = {'case_count': 1, 'suite_sha256': 'suite-v1'}
        batch = {'cases': [{'case_id': 'J2-03'}] if complete else [], 'environment': 'production',
                 'workspace_id': 'benchmark', 'model': 'deepseek-v4.1-flash', 'completed_at': date}
        v = {'version': version, 'git_commit': version + '-commit'}
        result = {'qa_version_start': v, 'qa_version_end': v if end is None else end,
                  'finalized_at': date, 'always_assert': {'score': score}, 'status': 'failed' if score == '0.00' else 'passed'}
        for file, data in [('run-manifest.json', manifest), ('batch-result.json', batch),
                           ('results/J2-03/result.json', result)]:
            (p / file).write_text(json.dumps(data))
        return p

    def test_latest_different_release_includes_low_score_and_skips_same_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_fixture(root, 'older', 'v1', '2026-11-25T01:00:00+00:00')
            failed = self.run_fixture(root, 'latest-previous', 'v2', '2026-11-26T01:00:00+00:00', score='0.00')
            self.run_fixture(root, 'same-version', 'v3', '2026-11-27T01:00:00+00:00')
            self.run_fixture(root, 'unfinished', 'v4', '2026-11-27T02:00:00+00:00', complete=False)
            current = self.run_fixture(root, 'current', 'v3', '2026-11-27T03:00:00+00:00')
            selected = select_previous_release(current, root)
            self.assertEqual(selected['selected_run'], str(failed.resolve()))
            self.assertEqual(selected['baseline_version'][0], 'v2')
            self.assertIn('same_release', [r['reason'] for r in selected['skipped']])

    def test_changed_version_run_and_future_run_are_not_previous_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_fixture(root, 'future', 'v2', '2026-11-28T01:00:00+00:00')
            self.run_fixture(root, 'mixed', 'v1', '2026-11-26T01:00:00+00:00', end={'version': 'v2'})
            current = self.run_fixture(root, 'current', 'v3', '2026-11-27T03:00:00+00:00')
            self.assertIsNone(select_previous_release(current, root)['selected_run'])

    def test_reexport_does_not_make_an_old_test_the_latest_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.run_fixture(root, 'reexported', 'v1', '2026-11-25T01:00:00+00:00')
            batch_path = old / 'batch-result.json'
            batch = json.loads(batch_path.read_text())
            batch['completed_at'] = '2026-11-27T02:00:00+00:00'
            batch_path.write_text(json.dumps(batch))
            latest = self.run_fixture(root, 'latest', 'v2', '2026-11-26T01:00:00+00:00')
            current = self.run_fixture(root, 'current', 'v3', '2026-11-27T03:00:00+00:00')
            self.assertEqual(select_previous_release(current, root)['selected_run'], str(latest.resolve()))


if __name__ == '__main__':
    unittest.main()
