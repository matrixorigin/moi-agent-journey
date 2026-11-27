import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from journey.catalog import ROOT, load_case
from journey.j2_scoring.__main__ import main
from journey.j2_scoring.compare import build_comparison
from journey.j2_scoring.runs import DEFAULT_SUITE, load_results, load_suite
from journey.j2_scoring.score import expected_check_count


def write_run(root, *, partial=False, missing=False):
    root.mkdir()
    items = [{'id': 'J2-13', 'domain': 'tools', 'executor': 'functional'},
             {'id': 'J2-37', 'domain': 'feedback', 'executor': 'functional'}]
    batch = {'model': 'tested-model', 'environment': 'qa', 'workspace_id': 'test-workspace', 'cases': []}
    for item in items:
        cid = item['id']
        if missing and cid == 'J2-37':
            continue
        count = expected_check_count(load_case(cid))
        checks = [{'session': 'fixture', 'rule': 'checkpoint-%s' % i,
                   'passed': not (partial and cid == 'J2-13' and i >= 2)} for i in range(count)]
        version = {'version': 'v1', 'git_commit': 'release-1'}
        result = {'case_id': cid, 'workspace': 'journey-2', 'status': 'passed',
                  'validation': {'checks': checks}, 'harness_errors': [], 'failures': [],
                  'qa_version_start': version, 'qa_version_end': version, 'task_seconds': 12,
                  'always_assert': {'score': '50.00' if partial else '100.00'}}
        directory = root / 'results' / cid
        directory.mkdir(parents=True)
        (directory / 'result.json').write_text(json.dumps(result))
        metadata = {'case_sha256': hashlib.sha256((ROOT / 'cases/journey-2' / cid / 'case.json').read_bytes()).hexdigest(),
                    'suite_sha256': 'frozen-suite'}
        # Only attempt 2 exists: comparisons must follow the batch-selected attempt.
        attempt = root / 'case-runs' / cid / 'attempts' / cid / 'attempt-02'
        attempt.mkdir(parents=True)
        (attempt / 'self-benchmark.json').write_text(json.dumps(metadata))
        batch['cases'].append({'case_id': cid, 'selected_attempt': 2, 'attempts': []})
    (root / 'batch-result.json').write_text(json.dumps(batch))
    return items


class J2ScoreRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old, self.new = self.root / 'old', self.root / 'new'
        items = write_run(self.old)
        write_run(self.new, partial=True)
        self.suite = self.root / 'suite.json'
        self.suite.write_text(json.dumps({'suite_id': 'synthetic-j2-test', 'cases': items}))

    def compare(self):
        return build_comparison(self.suite, self.old, self.new)

    def mutate(self, relative, modify):
        path = self.new / relative
        value = json.loads(path.read_text())
        modify(value)
        path.write_text(json.dumps(value))

    def test_current_suite_is_76_existing_cases_and_has_no_environment_binding(self):
        raw = json.loads(DEFAULT_SUITE.read_text())
        suite = load_suite(DEFAULT_SUITE)
        self.assertEqual(len(suite['cases']), 76)
        self.assertNotIn('J2-126', [c['id'] for c in suite['cases']])
        self.assertNotIn('environments', raw)
        self.assertFalse(raw['policy']['overall_score'])
        ids = [c['checkpoint_id'] for case in suite['_registry']['cases'] for c in case['checkpoints']]
        self.assertEqual(len(ids), len(set(ids)))

    def test_selected_attempt_scores_and_module_means_are_comparable(self):
        report = self.compare()
        row = report['cases'][0]
        self.assertEqual(row['comparison_status'], 'comparable')
        self.assertEqual(row['current']['score'], '50.00')
        self.assertEqual(row['score_delta_current_minus_baseline'], '-50.00')
        self.assertEqual(report['modules'][0]['paired_delta'], '-50.00')
        self.assertEqual(report['current']['scored'], 2)
        self.assertNotIn('score', report['current'])
        self.assertNotIn('gate_status', json.dumps(report))

    def test_missing_results_stay_in_the_coverage_denominator(self):
        (self.new / 'results/J2-37/result.json').unlink()
        report = self.compare()
        self.assertEqual(report['current']['total'], 2)
        self.assertEqual(report['current']['scored'], 1)
        self.assertEqual(report['current']['coverage'], '50.00')
        self.assertEqual(report['cases'][1]['current']['excluded_reason'], 'not_run')
        self.assertIsNone(report['modules'][1]['current_score'])

    def test_changed_execution_context_never_produces_deltas(self):
        original = (self.new / 'batch-result.json').read_text()
        for field in ('model', 'environment', 'workspace_id'):
            with self.subTest(field=field):
                (self.new / 'batch-result.json').write_text(original)
                self.mutate('batch-result.json', lambda v: v.update({field: 'different'}))
                report = self.compare()
                self.assertTrue(all(c['comparison_status'] == 'execution_context_changed' for c in report['cases']))
                self.assertTrue(all(c['score_delta_current_minus_baseline'] is None and
                                    c['task_seconds_delta_current_minus_baseline'] is None for c in report['cases']))

    def test_missing_or_changed_case_identity_is_not_comparable(self):
        path = 'case-runs/J2-13/attempts/J2-13/attempt-02/self-benchmark.json'
        for value in (None, 'different-definition'):
            with self.subTest(value=value):
                self.mutate(path, lambda v: v.update(case_sha256=value))
                self.assertEqual(self.compare()['cases'][0]['comparison_status'], 'case_definition_changed')

    def test_suite_changes_require_explicit_complete_projection(self):
        self.mutate('case-runs/J2-13/attempts/J2-13/attempt-02/self-benchmark.json',
                    lambda v: v.update(suite_sha256='different-suite'))
        self.assertEqual(self.compare()['cases'][0]['comparison_status'], 'suite_changed')
        report = build_comparison(self.suite, self.old, self.new, allow_suite_projection=True)
        self.assertEqual(report['cases'][0]['comparison_status'], 'comparable')
        (self.new / 'results/J2-37/result.json').unlink()
        with self.assertRaisesRegex(ValueError, 'complete_case_coverage'):
            build_comparison(self.suite, self.old, self.new, allow_suite_projection=True)

    def test_version_changes_and_missing_versions_do_not_produce_deltas(self):
        for value in (None, {'version': 'v1'}, {'version': 'v2', 'git_commit': 'release-2'}):
            with self.subTest(value=value):
                self.mutate('results/J2-13/result.json', lambda v: v.update(qa_version_end=value))
                row = self.compare()['cases'][0]
                self.assertEqual(row['comparison_status'], 'version_mismatch')
                self.assertIsNone(row['score_delta_current_minus_baseline'])

    def test_foreign_sql_multiplier_is_preserved_as_unscored_not_reinterpreted(self):
        self.mutate('results/J2-13/result.json',
                    lambda v: v.update(always_assert={'score': '25.00', 'path_factor': '0.50'}))
        row = self.compare()['cases'][0]['current']
        self.assertIsNone(row['score'])
        self.assertEqual(row['source_score'], '25.00')
        self.assertEqual(row['excluded_reason'], 'unsupported_scoring_policy')

    def test_extra_or_duplicate_boolean_payloads_do_not_inflate_the_score(self):
        self.mutate('results/J2-13/result.json',
                    lambda v: v['validation']['checks'].append({'passed': True, 'rule': 'extra'}))
        row = self.compare()['cases'][0]['current']
        self.assertIsNone(row['score'])
        self.assertEqual(row['excluded_reason'], 'checkpoints_extra')

    def test_result_path_identity_cannot_select_a_different_case(self):
        self.mutate('results/J2-13/result.json', lambda v: v.update(case_id='J2-37'))
        with self.assertRaisesRegex(ValueError, 'result_case_identity_mismatch'):
            load_results(self.new)

    def test_j3_and_excluded_cases_cannot_enter_this_suite(self):
        for item in ({'id': 'J3-48', 'domain': 'tools'}, {'id': 'J2-13', 'domain': 'knowledge'}):
            with self.subTest(item=item):
                self.suite.write_text(json.dumps({'suite_id': 'invalid', 'cases': [item]}))
                with self.assertRaises(ValueError):
                    load_suite(self.suite)

    def test_cli_is_offline_preserves_inputs_and_refuses_overwrite(self):
        before = {p: p.read_bytes() for p in self.new.rglob('*.json')}
        out = self.root / 'report'
        args = ['compare', '--suite', str(self.suite), '--current-run', str(self.new),
                '--baseline-run', str(self.old), '--output', str(out)]
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            self.assertEqual(main(args), 0)
            with self.assertRaises(FileExistsError):
                main(args)
        self.assertEqual(set(p.name for p in out.iterdir()), {'comparison.json', 'results.csv', 'report.md', 'report.html'})
        self.assertEqual(before, {p: p.read_bytes() for p in self.new.rglob('*.json')})
        self.assertNotIn('<script src=', (out / 'report.html').read_text())

    def test_first_baseline_has_no_fabricated_delta(self):
        report = build_comparison(self.suite, None, self.new)
        self.assertFalse(report['baseline_available'])
        self.assertTrue(all(c['comparison_status'] == 'no_baseline' for c in report['cases']))

    def test_persistent_case_requires_known_unchanged_mode_and_history_context(self):
        suite = {'suite_id': 'persistent-test', 'cases': [
            {'id': 'J2-132', 'domain': 'conversation', 'executor': 'persistent'}]}
        self.suite.write_text(json.dumps(suite))
        for run in (self.old, self.new):
            source = run / 'results/J2-13/result.json'
            value = json.loads(source.read_text())
            value['case_id'] = 'J2-132'
            count = expected_check_count(load_case('J2-132'))
            value['validation']['checks'] = [{'passed': True, 'rule': 'check-%d' % i} for i in range(count)]
            value['persistent_conversation'] = {'mode': 'image', 'continuity': 'checkpoint'}
            output = run / 'results/J2-132'
            output.mkdir()
            (output / 'result.json').write_text(json.dumps(value))
            metadata = run / 'case-runs/J2-132/attempts/J2-132/attempt-01'
            metadata.mkdir(parents=True)
            (metadata / 'self-benchmark.json').write_text(json.dumps(
                {'case_sha256': hashlib.sha256((ROOT / 'cases/journey-2/J2-132/case.json').read_bytes()).hexdigest(),
                 'suite_sha256': 'same-suite'}))
        self.assertEqual(self.compare()['cases'][0]['comparison_status'], 'comparable')
        for state in ({'mode': 'file', 'continuity': 'checkpoint'}, {},
                      {'mode': 'image', 'continuity': 'bootstrap'}):
            with self.subTest(state=state):
                self.mutate('results/J2-132/result.json', lambda v: v.update(persistent_conversation=state))
                row = self.compare()['cases'][0]
                self.assertEqual(row['comparison_status'], 'persistent_context_changed')
                self.assertIsNone(row['score_delta_current_minus_baseline'])
