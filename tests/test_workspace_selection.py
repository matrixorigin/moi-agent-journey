import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from journey.catalog import ROOT, journey_label, main
from journey.report import aggregate


class WorkspaceSelectionTests(unittest.TestCase):
    def plan(self, selection):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            stdout = io.StringIO()
            with patch.object(sys, 'argv', ['catalog', 'plan-workspaces', selection]), \
                    patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}), contextlib.redirect_stdout(stdout):
                self.assertEqual(main(), 0)
            matrix = json.loads(stdout.getvalue())
            self.assertEqual(output.read_text(), 'matrix=' + stdout.getvalue())
            return matrix['include']

    def test_display_selection_and_workspace_id_route_to_the_same_single_suite(self):
        config = json.loads((ROOT / 'suites.json').read_text())
        for suite in config['journeys']:
            with self.subTest(workspace=suite['workspace']):
                label = journey_label(suite)
                expected = [{'workspace': suite['workspace'], 'display_name': label}]
                self.assertEqual(self.plan(label), expected)
                self.assertEqual(self.plan(suite['workspace']), expected)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    report = aggregate(root / 'source', root / 'bundle', label)
                    self.assertEqual(report['workspace_selection'], suite['workspace'])
                    self.assertEqual([item['workspace'] for item in report['journeys']], [suite['workspace']])
                    self.assertEqual([item['case_id'] for item in report['cases']], suite['cases'])

    def test_all_preserves_every_workspace_including_empty_suites(self):
        self.assertEqual([item['workspace'] for item in self.plan('all')],
                         ['journey-1', 'journey-2', 'journey-3', 'journey-4', 'journey-5'])

    def test_unknown_or_stale_labels_do_not_run_a_different_scope(self):
        for selection in ('journey-9', 'journey-2 (wrong subtitle)', ''):
            with self.subTest(selection=selection), self.assertRaisesRegex(ValueError, 'unknown_workspace'):
                self.plan(selection)


if __name__ == '__main__':
    unittest.main()
