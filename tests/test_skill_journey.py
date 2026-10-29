import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence
from journey.functional_case import FunctionalExecution
from journey.skill_journey import SkillJourney, same_skill_content
from test_journeys import qa_fixture


class SkillJourneyTests(unittest.TestCase):
    def journey(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        execution = SimpleNamespace(root='/workspaces/ws', model='model', work=Path(directory.name),
            case={'sessions': [{'id': 'skill'}]}, client=Mock(deadline=time.monotonic() + 30),
            report={'run_id': '123', 'harness_errors': []}, checkpoint=lambda: None,
            evidence=Evidence(Path(directory.name)))
        journey = SkillJourney(execution, {'mode': 'versions', 'marker': 'current', 'initial_marker': 'old'}, {}, [])
        return journey, execution

    def test_skill_comparison_only_ignores_timestamp_precision(self):
        before = {'id': 'skill', 'version': 1, 'status': 'active', 'instruction': {'body': 'one'},
                  'updated_at': '2026-10-29T00:00:00.123Z'}
        after = {**before, 'updated_at': '2026-10-29T00:00:00Z'}
        self.assertTrue(same_skill_content(before, after))
        for field, value in [('id', 'other'), ('version', 2), ('status', 'disabled'), ('instruction', {'body': 'two'})]:
            self.assertFalse(same_skill_content(before, {**after, field: value}))

    def test_history_rejects_duplicate_missing_and_foreign_snapshots(self):
        journey, _ = self.journey()
        item = {'skill_id': journey.skill_id, 'version': 1,
                'spec_snapshot': {'id': journey.skill_id, 'version': 1, 'instruction': {'body': 'one'}}}
        self.assertEqual(journey.snapshots({'items': [item], 'total': 1}), {1: item['spec_snapshot']})
        for history in ({'items': [item, item], 'total': 2}, {'items': [item], 'total': 2},
                        {'items': [{**item, 'skill_id': 'other'}], 'total': 1},
                        {'items': [{**item, 'spec_snapshot': {'id': 'other', 'version': 1}}], 'total': 1}):
            with self.assertRaises(ProductCaseFailure):
                journey.snapshots(history)

    def test_status_change_cannot_silently_replace_skill_content(self):
        journey, execution = self.journey()
        before = {'id': journey.skill_id, 'workspace_id': 'ws', 'name': journey.name,
                  'status': 'active', 'instruction': {'body': 'current'}}
        after = {**before, 'status': 'disabled', 'instruction': {'body': 'changed'}}
        execution.client.product.side_effect = [before, after, after]
        with self.assertRaises(ProductCaseFailure):
            journey.set_status('disabled')
        self.assertFalse(journey.checks[-1]['passed'])

    def test_binding_catalog_does_not_hide_missing_duplicate_or_foreign_skill(self):
        journey, execution = self.journey()
        own = {'id': journey.skill_id, 'workspace_id': 'ws'}
        builtin = {'id': 'builtin', 'workspace_id': 'system'}
        detail = {'agent_id': journey.agent_id, 'agent_workspace_id': 'ws', 'skills': [builtin, own]}
        execution.client.product.return_value = detail
        journey.check_binding()
        for skills in ([builtin], [own, own], [builtin, {**own, 'workspace_id': 'other'}]):
            execution.client.product.return_value = {**detail, 'skills': skills}
            with self.assertRaises(ProductCaseFailure):
                journey.check_binding()

    def test_candidate_cleanup_failure_is_not_reported_as_completed(self):
        journey, execution = self.journey()
        journey.builder = Mock()
        journey.builder.cleanup.side_effect = lambda: execution.report['harness_errors'].append(
            {'stage': 'cleanup_agent', 'reason': 'http_error', 'http_status': 409})
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'], 'incomplete')

    def test_cleanup_does_not_delete_foreign_or_still_running_resources(self):
        journey, execution = self.journey()
        journey.skill_pending = True
        execution.client.product.return_value = {'id': journey.skill_id, 'workspace_id': 'other', 'name': journey.name}
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'], 'incomplete')
        self.assertFalse(any(call.args[0] == 'DELETE' for call in execution.client.product.call_args_list))
        execution.client.product.reset_mock()
        journey.cleanup(dependencies_confirmed=False)
        execution.client.product.assert_not_called()

    def test_cleanup_confirms_unbinding_and_actual_deletion(self):
        journey, execution = self.journey()
        journey.agent_pending = journey.skill_pending = True
        agent = {'id': journey.agent_id, 'workspace_id': 'ws', 'name': journey.agent_id}
        skill = {'id': journey.skill_id, 'workspace_id': 'ws', 'name': journey.name}
        execution.client.product.side_effect = [agent, agent, {'status': 'archived'}, {}, {},
                                                 skill, skill, {}, Failure('http_error', 404)]
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'], 'completed')
        self.assertEqual(execution.report['harness_errors'], [])

    def test_skill_preparation_failure_is_reported_and_cleanup_still_runs(self):
        with qa_fixture('success') as (base, _):
            case = {'id': 'J2-63', 'title': 'skill', 'workspace': 'journey-1', 'executor': 'api',
                    'timeout_seconds': 30, 'skill_journey': {}, 'sessions': [{'id': 'skill', 'turns': []}]}
            instance = Mock()
            def fail_prepare():
                execution.report['validation'] = {'checks': [{'session': 'skill', 'rule': 'create', 'passed': False}]}
                raise ProductCaseFailure('skill_creation_rejected')
            instance.prepare.side_effect = fail_prepare
            execution = FunctionalExecution(base.client, case, base.evidence, base.work, 'model')
            with patch('journey.functional_case.SkillJourney', return_value=instance):
                result = execution.run()
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['harness_errors'], [])
            instance.cleanup.assert_called_once_with(dependencies_confirmed=True)


if __name__ == '__main__':
    unittest.main()
