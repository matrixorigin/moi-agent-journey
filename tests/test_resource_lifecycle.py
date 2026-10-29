import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence
from journey.resource_lifecycle import ResourceLifecycleJourney, unavailable_resource_check
from journey.resource_journey import ResourceJourney


class LifecycleAPI:
    def __init__(self, kind, defect=None):
        self.kind, self.defect = kind, defect
        self.deadline, self.observer = time.monotonic() + 60, None
        self.writes = []
        self.resource = {'id': 'resource', 'name': 'resource', 'workspace_id': 'ws',
                         'status': 'active', 'version': 1, 'instruction': {'body': 'original'}}
        self.agents = {id: {'id': id, 'name': id, 'workspace_id': 'ws', 'status': 'active',
                           'binding': {kind + '_refs': [{'id': 'resource', 'workspace_id': 'ws'}]}}
                       for id in ('owner', 'peer')}

    def product(self, method, path, body=None):
        relative = path.removeprefix('/workspaces/ws/')
        collection, id = relative.split('/', 1)
        item = self.agents.get(id) if collection == 'agents' else self.resource
        status, reason = 200, None
        if method == 'GET':
            if item is None:
                raise Failure('http_error', 404)
            return copy.deepcopy(item)
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == 'DELETE':
            protected = any(a['status'] != 'archived' for a in self.agents.values())
            if protected and self.defect != 'delete_shared':
                status, reason = 422, self.kind.upper() + '_IN_USE'
                if self.defect == 'reject_but_delete':
                    self.resource = None
                if self.defect == 'wrong_reason':
                    reason = 'UNRELATED_FAILURE'
            elif self.defect == 'archived_protected':
                status, reason = 422, self.kind.upper() + '_IN_USE'
            else:
                self.resource = None
                if self.defect == 'delete_rewrites_history':
                    self.agents['owner']['binding'] = {}
        elif collection == 'agents':
            invalid = body['status'] == 'active' and (
                self.resource is None or self.resource['status'] != 'active')
            if invalid and self.defect != 'invalid_restore_allowed':
                status = 400
                if self.defect == 'reject_but_activate':
                    item['status'] = 'active'
            else:
                if self.defect != 'archive_not_persisted':
                    item.update(body)
                if self.defect == 'archive_loses_binding':
                    item['binding'] = {}
        else:
            item.update(body)
            item['version'] += 1
            if self.defect == 'write_changes_instruction':
                item['instruction'] = {'body': 'corrupted'}
        response = {'code': 2, 'details': {'reason': reason}} if status != 200 else {'code': 0}
        if self.observer:
            self.observer({'method': method, 'path': path, 'status': status,
                           'response': {'error_body': json.dumps(response)} if status != 200 else response})
        if status != 200:
            raise Failure('http_error', status)
        return copy.deepcopy(item or {})


class ResourceLifecycleTests(unittest.TestCase):
    def fixture(self, kind='skill', mode='shared', defect=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        client = LifecycleAPI(kind, defect)
        execution = SimpleNamespace(root='/workspaces/ws', client=client, model='model',
            case={'sessions': [{'id': 'test'}]}, report={'run_id': 'unique', 'harness_errors': []},
            evidence=Evidence(Path(directory.name)), checkpoint=Mock())
        definition = {'kind': kind, 'mode': mode}
        if mode == 'isolation':
            definition['status'] = 'disabled'
        journey = ResourceLifecycleJourney(execution, definition, [])
        owners = ['owner', 'peer'] if mode != 'reactivation' else ['owner']
        client.agents = {k: v for k, v in client.agents.items() if k in owners}
        journey.fixtures = [SimpleNamespace(agent_id=id, agent_path='/agents/' + id,
            skill_id='resource', skill_path='/skills/resource', name='resource', marker='marker') for id in owners]
        journey.primary = journey.fixtures[0]
        journey.peer = journey.fixtures[-1] if len(owners) > 1 else None
        journey.primary_resource = {'id': 'resource', 'name': 'resource', 'path': '/' + kind + 's/resource'}
        return journey, client

    def test_archive_only_releases_last_reference_and_preserves_history(self):
        for kind in ('tool', 'skill'):
            journey, client = self.fixture(kind)
            before = copy.deepcopy(client.agents)
            journey.apply('shared_references')
            self.assertIsNone(client.resource)
            for id, agent in client.agents.items():
                self.assertEqual(agent, {**before[id], 'status': 'archived'})
            self.assertTrue(all(check['passed'] for check in journey.checks))
            self.assertEqual(journey.state['actions'], ['shared_references'])

    def test_reference_guard_false_success_and_unrelated_errors_are_detected(self):
        for defect in ('delete_shared', 'wrong_reason', 'archived_protected', 'archive_loses_binding',
                       'delete_rewrites_history', 'write_changes_instruction'):
            with self.subTest(defect=defect):
                journey, _ = self.fixture(defect=defect)
                with self.assertRaises(ProductCaseFailure):
                    journey.apply('shared_references')
                self.assertFalse(journey.checks[-1]['passed'])
                self.assertEqual(journey.state['actions'], [])

    def test_disabled_dependency_can_be_restored_but_deleted_dependency_cannot(self):
        for kind in ('tool', 'skill'):
            journey, client = self.fixture(kind, 'reactivation')
            journey.apply('disable_and_reactivate')
            self.assertEqual(client.agents['owner']['status'], 'active')
            self.assertEqual(client.resource['status'], 'active')
            journey.apply('delete_and_reactivate')
            self.assertEqual(client.agents['owner']['status'], 'archived')
            self.assertIsNone(client.resource)
            self.assertTrue(all(check['passed'] for check in journey.checks))

    def test_invalid_reactivation_never_counts_as_success(self):
        for defect in ('invalid_restore_allowed', 'reject_but_activate'):
            journey, _ = self.fixture(mode='reactivation', defect=defect)
            with self.assertRaises(ProductCaseFailure):
                journey.apply('disable_and_reactivate')
            self.assertFalse(journey.checks[-1]['passed'])

    def test_all_inactive_states_change_only_resource_state(self):
        for status in ('disabled', 'archived', 'draft'):
            journey, client = self.fixture(mode='isolation')
            journey.status = status
            before = copy.deepcopy(client.agents)
            journey.apply('deactivate')
            self.assertEqual(client.resource['status'], status)
            self.assertEqual(client.agents, before)
            journey.apply('activate')
            self.assertEqual(client.resource['status'], 'active')
            self.assertEqual(client.resource['instruction'], {'body': 'original'})

    def test_no_cleanup_mutations_without_terminal_task_proof(self):
        journey, client = self.fixture()
        journey.cleanup(dependencies_confirmed=False)
        self.assertEqual(client.writes, [])
        self.assertEqual(journey.state['cleanup'], 'incomplete')
        self.assertEqual(journey.execution.report['harness_errors'][0]['reason'], 'dependent_task_unconfirmed')

    def cleanup_fixture(self, kind='skill', defect=None):
        journey, client = self.fixture(kind=kind, defect=defect)
        for f in journey.fixtures:
            f.execution, f.workspace, f.builder = journey.execution, 'ws', None
            f.agent_pending, f.skill_pending = True, True
            f.tools = [{'id': 'resource', 'name': 'resource'}]
            f.path = lambda tool: '/tools/' + tool['id']
            f.state = {'cleanup': 'pending', 'pending_ids': ['resource']}
            f.absent = MethodType(ResourceJourney.absent, f)
            f.archive_owned_agent = MethodType(ResourceJourney.archive_owned_agent, f)
        return journey, client

    def test_cleanup_archives_all_owners_before_deletion_without_rewriting_bindings(self):
        for kind in ('tool', 'skill'):
            journey, client = self.cleanup_fixture(kind)
            before = copy.deepcopy(client.agents)
            journey.cleanup(dependencies_confirmed=True)
            self.assertEqual([(method, path) for method, path, _ in client.writes], [
                ('PATCH', '/workspaces/ws/agents/owner'), ('PATCH', '/workspaces/ws/agents/peer'),
                ('DELETE', '/workspaces/ws/' + kind + 's/resource')])
            self.assertIsNone(client.resource)
            for id, agent in client.agents.items():
                self.assertEqual(agent, {**before[id], 'status': 'archived'})
            self.assertEqual(journey.state['cleanup'], 'completed')
            self.assertEqual(journey.execution.report['harness_errors'], [])

    def test_cleanup_preserves_deleted_dependency_history(self):
        journey, client = self.cleanup_fixture()
        client.resource = None
        for agent in client.agents.values():
            agent['status'] = 'archived'
        before = copy.deepcopy(client.agents)
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(client.writes, [])
        self.assertEqual(client.agents, before)
        self.assertEqual(journey.state['cleanup'], 'completed')

    def test_cleanup_never_deletes_foreign_or_unconfirmed_resources(self):
        for defect in ('foreign_resource', 'foreign_agent', 'archive_not_persisted'):
            journey, client = self.cleanup_fixture(defect=defect)
            if defect == 'foreign_resource':
                client.resource['workspace_id'] = 'foreign'
            elif defect == 'foreign_agent':
                client.agents['owner']['name'] = 'foreign'
            journey.cleanup(dependencies_confirmed=True)
            self.assertFalse(any(method == 'DELETE' for method, _, _ in client.writes), defect)
            self.assertEqual(journey.state['cleanup'], 'incomplete', defect)
            self.assertTrue(journey.execution.report['harness_errors'], defect)

    def test_cleanup_does_not_claim_resources_before_creation_was_attempted(self):
        for kind in ('tool', 'skill'):
            journey, client = self.cleanup_fixture(kind)
            for f in journey.fixtures:
                f.agent_pending, f.skill_pending = False, False
                f.state['pending_ids'] = []
            journey.cleanup(dependencies_confirmed=True)
            self.assertEqual(client.writes, [])
            self.assertIsNotNone(client.resource)
            self.assertEqual(journey.state['cleanup'], 'completed')

    def test_inactive_resource_cannot_hide_success_or_missing_result(self):
        journey, _ = self.fixture()
        path = journey.execution.evidence.root / 'events.jsonl'
        for kind in ('tool', 'skill'):
            call = {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'resource' if kind == 'tool' else 'skill',
                'call_id': 'c', 'arguments': {'skill_name': 'resource'}}}
            result = {'task_id': 'task', 'kind': 'tool_result', 'payload': {**call['payload'], 'success': False}}
            for rows, expected in (([], True), ([call], False), ([call, result], True),
                    ([call, {**result, 'payload': {**result['payload'], 'success': True}}], False),
                    ([call, {**result, 'task_id': 'other'}], False)):
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                self.assertEqual(unavailable_resource_check(path, 'task', kind, 'resource', 'name'), expected)

    def test_invalid_state_and_mode_are_rejected_before_writes(self):
        journey, client = self.fixture()
        for spec in ({'kind': 'skill', 'mode': 'isolation', 'status': 'inactive'},
                     {'kind': 'skill', 'mode': 'shared', 'status': 'disabled'},
                     {'kind': 'unknown', 'mode': 'shared'}):
            with self.assertRaises(Failure):
                ResourceLifecycleJourney(journey.execution, spec, [])
        self.assertEqual(client.writes, [])


if __name__ == '__main__':
    unittest.main()
