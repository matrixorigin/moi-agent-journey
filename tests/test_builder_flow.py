import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from journey.builder_flow import AgentBuilderFlow, ProductCaseFailure, same_published_config
from journey.client import Failure
from journey.evidence import Evidence
from journey.run_case import Execution


def task(name, version, *, skills=(), tools=(), agent_id='test-agent', mode='create'):
    output = {'render_type': 'agent_candidate', 'workspace_id': 'ws', 'agent_id': agent_id,
              'candidate_version': version, 'source_digest': 'sha256:' + version,
              'status': 'valid', 'mode': mode, 'load_version': '1.1.0' if version == 'v3' else '1.0.0', 'display': {
                  'agent_id': agent_id, 'agent_name': name, 'model': 'model', 'skills': list(skills),
                  'tools': list(tools), 'knowledge_bases': [], 'channel_bindings': []},
              'resolved_refs': {'skills': [{'id': 'skill.xlsx', 'name': 'XLSX', 'workspace_id': 'system'}]
                                if skills else [],
                                'tools': [{'id': 'tool.search', 'name': 'Search', 'workspace_id': 'system'}]
                                if tools else []}}
    return {'kind': 'task', 'id': 'task-' + version, 'contextId': 'conversation',
            'status': {'state': 'completed', 'message': {'parts': [
                {'kind': 'data', 'data': {'output': {'output': output}}}]}}}


class FakeClient:
    def __init__(self, *, accept_old=False, published_skill=True):
        self.deadline = time.monotonic() + 30
        self.requests = 0
        self.agent = None
        self.versions = []
        self.calls = []
        self.accept_old, self.published_skill = accept_old, published_skill
        original = {'agent_id': 'test-agent', 'name': 'revised', 'description': 'old description',
                    'model_name': 'model', 'model_config_ref': 'config', 'agent_md': 'original prompt',
                    'tool_names': [], 'skill_names': ['XLSX'], 'knowledge_base_names': [],
                    'catalog_files': [], 'channel_bindings': [],
                    'resource_refs': {'skills': [{'id': 'skill.xlsx', 'name': 'XLSX', 'workspace_id': 'system'}],
                                      'tools': [], 'knowledge_bases': []}}
        revised = copy.deepcopy(original)
        revised['description'] = 'desired-description'
        self.current_versions = {'1.0.0': original, '1.1.0': revised}

    def product(self, method, path, body=None):
        self.requests += 1
        self.calls.append((method, path))
        if method == 'GET' and path.endswith('/current-agent'):
            version = path.split('/versions/')[1].split('/')[0]
            return copy.deepcopy(self.current_versions[version])
        if method == 'GET' and '/versions?' in path:
            skills = [{'name': 'XLSX', 'workspace_id': 'system'}] if self.published_skill else []
            return {'items': [{'version': version, 'agent_id': 'test-agent', 'status': 'runnable', 'manifest': {
                'id': 'test-agent', 'name': self.agent['name'], 'model': 'model',
                'moiResourceRefs': {'skills': skills, 'tools': [{'name': 'Search', 'workspace_id': 'system'}]}}}
                for version in self.versions]}
        if method == 'GET' and '/agents/' in path:
            if not self.agent:
                raise Failure('http_error', 404)
            return dict(self.agent)
        if method == 'PATCH':
            self.agent['status'] = body['status']
            return {'status': body['status']}
        if method == 'POST' and path.endswith('/cancel'):
            version = path.split('/candidate-versions/')[1].split('/')[0]
            return {'agent_id': 'test-agent', 'candidate_version': version, 'status': 'superseded'}
        if method == 'POST' and path.endswith('/commit'):
            if '/v1/' in path and not self.accept_old:
                raise Failure('http_error', 409)
            self.agent = {'id': 'test-agent', 'workspace_id': 'ws',
                          'name': 'original' if '/v1/' in path else 'revised', 'status': 'active'}
            version = '1.1.0' if '/v3/' in path else '1.0.0'
            self.versions.append(version)
            return {'agent_id': 'test-agent', 'workspace_id': 'ws', 'load_version': version,
                    'source_digest': body['source_digest']}
        raise AssertionError('unexpected request')


class BuilderFlowTests(unittest.TestCase):
    def flow(self, **options):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        client = FakeClient(**options)
        case = {'id': 'J2-08', 'title': 'builder', 'workspace': 'journey-2', 'timeout_seconds': 30}
        execution = Execution(client, case, Evidence(Path(tmp.name)), tmp.name, 'model')
        execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        checks = []
        return AgentBuilderFlow(execution, 'builder', checks, ['original', 'revised']), client, checks

    def test_cleanup_readback_keeps_network_failure_details(self):
        flow, client, _ = self.flow()
        flow.execution.conversations.append({'id': 'conversation', 'selector': flow.execution.selector})
        flow.execution.tasks.append({'phase': 'builder:create', 'conversation_id': 'conversation', 'task_id': 'task'})
        client.rpc = Mock(side_effect=Failure('transport_error', details={'category': 'dns', 'exception': 'gaierror'}))
        flow.cleanup()
        error = flow.execution.report['harness_errors'][0]
        self.assertEqual(error['reason'], 'candidate_readback:transport_error')
        self.assertEqual(error['details']['category'], 'dns')
        client.rpc.assert_called_once()

    def test_revision_rejects_old_candidate_and_publishes_the_new_identity(self):
        flow, client, checks = self.flow()
        flow.remember(task('original', 'v1'), 'original')
        flow.revise(task('revised', 'v2'), 'revised')
        agent = flow.commit()
        self.assertTrue(agent['verified'])
        self.assertEqual(agent['agent_id'], 'test-agent')
        self.assertEqual(len(checks), 12)
        self.assertTrue(all(check['passed'] for check in checks))
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')
        self.assertEqual(flow.execution.report['harness_errors'], [])

    def test_accepted_old_candidate_fails_and_only_our_agent_is_archived(self):
        flow, client, checks = self.flow(accept_old=True)
        flow.remember(task('original', 'v1'), 'original')
        with self.assertRaises(ProductCaseFailure):
            flow.revise(task('revised', 'v2'), 'revised')
        self.assertFalse(next(item for item in checks if item['rule'] == '旧候选不能再发布')['passed'])
        self.assertFalse(any('/v2/' in path and method == 'POST' for method, path in client.calls))
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')

    def test_existing_agent_with_same_name_is_never_archived(self):
        flow, client, checks = self.flow()
        client.agent = {'id': 'test-agent', 'workspace_id': 'ws', 'name': 'revised', 'status': 'active'}
        with self.assertRaises(ProductCaseFailure):
            flow.remember(task('revised', 'v2'), 'revised')
        self.assertFalse(next(item for item in checks if item['rule'] == '确认前没有发布智能体')['passed'])
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'active')
        self.assertFalse(any(method == 'PATCH' for method, _ in client.calls))
        self.assertTrue(any('cleanup_agent_ownership_unverified' in item.get('reason', '')
                            for item in flow.execution.report['harness_errors']))

    def test_unexpected_agent_after_absence_without_our_commit_is_not_archived(self):
        flow, client, _ = self.flow()
        flow.remember(task('revised', 'v2'), 'revised')
        client.agent = {'id': 'test-agent', 'workspace_id': 'ws', 'name': 'revised', 'status': 'active'}
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'active')
        self.assertFalse(any(method == 'PATCH' for method, _ in client.calls))

    def test_mismatched_commit_response_does_not_authorize_archive(self):
        flow, client, _ = self.flow()
        flow.remember(task('revised', 'v2'), 'revised')
        product = client.product
        def wrong_identity(method, path, body=None):
            result = product(method, path, body)
            if method == 'POST' and path.endswith('/commit'):
                result['source_digest'] = 'other-digest'
            return result
        client.product = wrong_identity
        with self.assertRaises(ProductCaseFailure):
            flow.commit()
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'active')
        self.assertFalse(any(method == 'PATCH' for method, _ in client.calls))
        self.assertTrue(any('cleanup_agent_ownership_unverified' in item.get('reason', '')
                            for item in flow.execution.report['harness_errors']))

    def test_reordering_unchanged_resources_is_not_a_configuration_change(self):
        flow, _, checks = self.flow()
        first = task('original', 'v1', skills=['XLSX', 'Other'])
        second = task('revised', 'v2', skills=['Other', 'XLSX'])
        for item in (first, second):
            output = item['status']['message']['parts'][0]['data']['output']['output']
            output['resolved_refs']['skills'].append({'id': 'skill.other', 'name': 'Other', 'workspace_id': 'system'})
        second['status']['message']['parts'][0]['data']['output']['output']['resolved_refs']['skills'].reverse()
        flow.remember(first, 'original')
        flow.revise(second, 'revised')
        self.assertTrue(next(item for item in checks if item['rule'] == '未修改的模型与资源绑定保持不变')['passed'])

    def test_candidate_requires_exact_skill_and_published_binding(self):
        flow, client, checks = self.flow(published_skill=False)
        flow.remember(task('revised', 'v2', skills=['XLSX']), 'revised')
        with self.assertRaises(ProductCaseFailure):
            flow.require_resource('skills', 'different-skill')
        skill = flow.require_resource('skills', 'skill.xlsx')
        with self.assertRaises(ProductCaseFailure):
            flow.commit([skill])
        self.assertFalse(next(item for item in checks if item['rule'] == '发布版本保留指定配置')['passed'])
        self.assertTrue(flow.created['verified'])  # Delivered, but incorrectly configured.
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')

    def test_tool_reference_is_checked_against_the_published_version(self):
        flow, client, checks = self.flow()
        flow.remember(task('revised', 'v2', tools=['Search']), 'revised')
        resource = flow.require_resource('tools', 'tool.search')
        self.assertTrue(flow.commit([resource])['verified'])
        self.assertTrue(all(check['passed'] for check in checks))
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')

    def test_every_requested_binding_must_survive_publication(self):
        for published_skill in (True, False):
            with self.subTest(published_skill=published_skill):
                flow, client, checks = self.flow(published_skill=published_skill)
                flow.remember(task('revised', 'v2', skills=['XLSX'], tools=['Search']), 'revised')
                resources = [flow.require_resource('skills', 'skill.xlsx'),
                             flow.require_resource('tools', 'tool.search')]
                if published_skill:
                    self.assertTrue(flow.commit(resources)['verified'])
                else:
                    with self.assertRaises(ProductCaseFailure):
                        flow.commit(resources)
                self.assertEqual(next(item for item in checks if item['rule'] == '发布版本保留指定配置')['passed'],
                                 published_skill)
                flow.cleanup()
                self.assertEqual(client.agent['status'], 'archived')

    def test_revision_of_published_agent_creates_new_version_and_preserves_identity(self):
        flow, client, checks = self.flow()
        flow.remember(task('revised', 'v2'), 'revised')
        first = flow.commit()
        flow.revise_published(task('revised', 'v3', mode='revise'), 'revised')
        revised = flow.commit(expected_description='desired-description')
        self.assertNotEqual(first['version'], revised['version'])
        self.assertEqual(first['agent_id'], revised['agent_id'])
        self.assertTrue(all(item['passed'] for item in checks))
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')
        self.assertEqual(flow.execution.report['harness_errors'], [])

    def test_instruction_revision_requires_marker_and_preserves_other_configuration(self):
        for failure in (None, 'marker_missing', 'skill_changed'):
            with self.subTest(failure=failure):
                flow, client, checks = self.flow()
                flow.remember(task('revised', 'v2', skills=['XLSX']), 'revised')
                resource = flow.require_resource('skills', 'skill.xlsx')
                flow.commit([resource], capture_current=True)
                flow.revise_published(task('revised', 'v3', skills=['XLSX'], mode='revise'), 'revised')
                current = client.current_versions['1.1.0']
                current['description'] = client.current_versions['1.0.0']['description']
                current['agent_md'] = 'new prompt' if failure == 'marker_missing' else 'new prompt with unique-marker'
                if failure == 'skill_changed':
                    current['skill_names'] = []
                if failure:
                    with self.assertRaises(ProductCaseFailure):
                        flow.commit(expected_instruction='unique-marker')
                    self.assertFalse(all(check['passed'] for check in checks))
                else:
                    flow.commit(expected_instruction='unique-marker')
                    self.assertTrue(all(check['passed'] for check in checks))

    def test_uncommitted_revision_is_canceled_before_archiving_our_agent(self):
        flow, client, _ = self.flow()
        flow.remember(task('revised', 'v2'), 'revised')
        flow.commit()
        flow.revise_published(task('revised', 'v3', mode='revise'), 'revised')
        flow.cleanup()
        cancel = next(i for i, (method, path) in enumerate(client.calls) if method == 'POST' and path.endswith('/cancel'))
        archive = next(i for i, (method, _) in enumerate(client.calls) if method == 'PATCH')
        self.assertLess(cancel, archive)
        self.assertEqual(flow.execution.report['harness_errors'], [])

    def test_published_revision_retains_nonempty_skill_and_prompt(self):
        flow, client, checks = self.flow()
        flow.remember(task('revised', 'v2', skills=['XLSX']), 'revised')
        resource = flow.require_resource('skills', 'skill.xlsx')
        first = flow.commit([resource], capture_current=True)
        flow.revise_published(task('revised', 'v3', skills=['XLSX'], mode='revise'), 'revised')
        revised = flow.commit(expected_description='desired-description')
        self.assertNotEqual(first['version'], revised['version'])
        self.assertTrue(all(item['passed'] for item in checks))
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')

    def test_published_revision_losing_skill_or_prompt_is_a_product_failure(self):
        for changed in ('skill', 'prompt', 'model_config'):
            with self.subTest(changed=changed):
                flow, client, checks = self.flow()
                flow.remember(task('revised', 'v2', skills=['XLSX']), 'revised')
                resource = flow.require_resource('skills', 'skill.xlsx')
                flow.commit([resource], capture_current=True)
                flow.revise_published(task('revised', 'v3', skills=['XLSX'], mode='revise'), 'revised')
                current = client.current_versions['1.1.0']
                if changed == 'skill':
                    current['skill_names'] = []
                    current['resource_refs']['skills'] = []
                elif changed == 'prompt':
                    current['agent_md'] = 'unexpected prompt'
                else:
                    current['model_config_ref'] = 'different-config'
                with self.assertRaises(ProductCaseFailure):
                    flow.commit(expected_description='desired-description')
                self.assertFalse(next(item for item in checks if item['rule'] == '发布修订保留原提示与资源')['passed'])
                flow.cleanup()
                self.assertEqual(client.agent['status'], 'archived')
                self.assertEqual(flow.execution.report['harness_errors'], [])

    def test_published_resource_order_does_not_change_configuration(self):
        _, client, _ = self.flow()
        old = client.current_versions['1.0.0']
        old['skill_names'].append('Other')
        old['resource_refs']['skills'].append({'id': 'skill.other', 'name': 'Other', 'workspace_id': 'system'})
        updated = copy.deepcopy(old)
        updated['skill_names'].reverse()
        updated['resource_refs']['skills'].reverse()
        self.assertTrue(same_published_config(old, updated))
        del updated['model_name']
        self.assertFalse(same_published_config(old, updated))

    def test_initial_readback_must_include_requested_skill(self):
        flow, client, _ = self.flow()
        client.current_versions['1.0.0']['resource_refs']['skills'] = []
        flow.remember(task('revised', 'v2', skills=['XLSX']), 'revised')
        resource = flow.require_resource('skills', 'skill.xlsx')
        with self.assertRaises(ProductCaseFailure):
            flow.commit([resource], capture_current=True)
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')

    def test_published_revision_adds_and_removes_only_requested_resources(self):
        changes = {'skills': {'remove': ['skill.xlsx']}, 'tools': {'add': ['tool.search']}}
        flow, client, checks = self.flow()
        flow.remember(task('revised', 'v2', skills=['XLSX']), 'revised')
        resource = flow.require_resource('skills', 'skill.xlsx')
        flow.commit([resource], capture_current=True)
        flow.revise_published(task('revised', 'v3', tools=['Search'], mode='revise'), 'revised', changes)
        revised = client.current_versions['1.1.0']
        revised['description'] = client.current_versions['1.0.0']['description']
        revised['skill_names'] = []
        revised['tool_names'] = ['Search']
        revised['resource_refs']['skills'] = []
        revised['resource_refs']['tools'] = [{'id': 'tool.search', 'name': 'Search', 'workspace_id': 'system'}]
        flow.commit(changes=changes)
        self.assertTrue(all(item['passed'] for item in checks))
        flow.cleanup()
        self.assertEqual(client.agent['status'], 'archived')
        self.assertEqual(flow.execution.report['harness_errors'], [])

    def test_unrequested_published_changes_do_not_pass_resource_transition(self):
        changes = {'skills': {'remove': ['skill.xlsx']}, 'tools': {'add': ['tool.search']}}
        _, client, _ = self.flow()
        old = client.current_versions['1.0.0']
        new = copy.deepcopy(old)
        new['skill_names'] = []
        new['tool_names'] = ['Search']
        new['resource_refs']['skills'] = []
        new['resource_refs']['tools'] = [{'id': 'tool.search', 'name': 'Search', 'workspace_id': 'system'}]
        self.assertTrue(same_published_config(old, new, changes, keep_description=True))
        for field in ('description', 'agent_md', 'model_name'):
            bad = copy.deepcopy(new)
            bad[field] = 'unexpected'
            self.assertFalse(same_published_config(old, bad, changes, keep_description=True))
        bad = copy.deepcopy(new)
        bad['resource_refs']['skills'] = copy.deepcopy(old['resource_refs']['skills'])
        bad['skill_names'] = copy.deepcopy(old['skill_names'])
        self.assertFalse(same_published_config(old, bad, changes, keep_description=True))


if __name__ == '__main__':
    unittest.main()
