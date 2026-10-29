"""Regression journeys for live dependencies and archived Agent references."""
import json
import time

from .client import Failure, error_record
from .resource_journey import ResourceJourney
from .skill_journey import SkillJourney
from .tool_journey import ToolJourney, evaluate_http_tool
from .skill_checks import check_skill_call


def unavailable_resource_check(path, task_id, kind, resource_id, name):
    """An attempted rejected call is allowed; a successful or unsettled call is not."""
    calls, results = set(), {}
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict):
            continue
        tool, call_id = payload.get('tool_id'), payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        args = payload.get('arguments')
        if event.get('kind') == 'tool_call' and (
                (kind == 'tool' and tool in {resource_id, name}) or
                (kind == 'skill' and tool == 'skill' and isinstance(args, dict) and
                 args.get('skill_name') in {resource_id, name})):
            calls.add((tool, call_id))
        elif event.get('kind') == 'tool_result':
            results[(tool, call_id)] = payload.get('success')
    return all(results.get(key) is False for key in calls)


class ResourceLifecycleJourney(ResourceJourney):
    def __init__(self, execution, definition, checks):
        if (not isinstance(definition, dict) or definition.get('kind') not in {'skill', 'tool'} or
                definition.get('mode') not in {'isolation', 'shared', 'reactivation'}):
            raise Failure('invalid_resource_lifecycle')
        self.kind, self.mode = definition['kind'], definition['mode']
        expected = {'kind', 'mode', 'status'} if self.mode == 'isolation' else {'kind', 'mode'}
        if set(definition) != expected or (self.mode == 'isolation' and
                definition['status'] not in {'draft', 'disabled', 'archived'}):
            raise Failure('invalid_resource_lifecycle')
        self.execution, self.checks = execution, checks
        self.workspace = execution.root.rsplit('/', 1)[-1]
        self.session_id = execution.case['sessions'][0]['id']
        self.status = definition.get('status')
        self.fixtures = []
        self.last_exchange = None
        self.previous_observer = execution.client.observer
        execution.client.observer = self.observe
        self.state = {'kind': self.kind, 'mode': self.mode, 'inactive_status': self.status,
                      'actions': [], 'cleanup': 'pending'}
        execution.report['resource_lifecycle'] = self.state

    def observe(self, exchange):
        self.last_exchange = exchange
        if self.previous_observer:
            self.previous_observer(exchange)
        if exchange.get('path', '').startswith(self.execution.root) or exchange.get('path') == '/agents/a2a':
            self.execution.evidence.append('lifecycle-http.jsonl', exchange)

    def fixture(self, suffix):
        if self.kind == 'skill':
            marker = 'PROJECT-' + self.execution.report['run_id'][:8] + suffix
            fixture = SkillJourney(self.execution, {'mode': 'use', 'marker': marker,
                'initial_marker': 'UNUSED-' + marker}, {}, self.checks, identity_suffix=suffix)
        else:
            fixture = ToolJourney(self.execution, {'mode': 'use'}, self.checks, identity_suffix=suffix)
        # Remember before any write, including publication with an uncertain response.
        self.fixtures.append(fixture)
        fixture.prepare()
        return fixture

    def resource(self, fixture):
        if self.kind == 'skill':
            return {'id': fixture.skill_id, 'name': fixture.name, 'path': fixture.skill_path,
                    'marker': fixture.marker}
        tool = fixture.tools[0]
        return {'id': tool['id'], 'name': tool['name'], 'path': fixture.path(tool), 'marker': 'J2-07'}

    def prepare(self):
        self.primary = self.fixture('')
        self.primary_resource = self.resource(self.primary)
        self.peer = self.fixture('-peer') if self.mode != 'reactivation' else None
        if self.peer:
            self.peer_resource = self.resource(self.peer)
            owner, resource = ((self.primary, self.peer_resource) if self.mode == 'isolation'
                               else (self.peer, self.primary_resource))
            ref = {'kind': self.kind, 'id': resource['id'], 'workspace_id': self.workspace}
            owner.request('PATCH', owner.agent_path + '/bindings',
                          {'binding_ops': [{'op': 'add', 'kind': self.kind, 'ref': ref}]})
            binding = owner.request('GET', owner.agent_path + '/bindings')
            self.record('本次对照/共享资源已精确绑定', sum(
                item.get('id') == resource['id'] and item.get('workspace_id') == self.workspace
                for item in binding.get(self.kind + 's', []) if isinstance(item, dict)) == 1,
                'lifecycle_binding_missing', required=True)
        self.state['agents'] = [fixture.agent_id for fixture in self.fixtures]
        self.state['resources'] = [self.resource(fixture)['id'] for fixture in self.fixtures]
        return {prefix + key: value for prefix, resource in (
                ('primary_', self.primary_resource),
                ('healthy_', self.peer_resource if self.peer else self.primary_resource))
                for key, value in resource.items() if key != 'path'}

    def selector(self):
        return (self.peer if self.mode == 'shared' else self.primary).selector()

    def read_resource(self):
        return self.request('GET', self.primary_resource['path'])

    def update_resource(self, patch):
        before = self.read_resource()
        self.request('PATCH', self.primary_resource['path'], patch)
        after = self.read_resource()
        retained = ('id', 'workspace_id', 'name', 'instruction', 'source_ref', 'input_schema', 'output_schema')
        self.record('资源修改持久化且保留其他定义 ' + str(patch),
            all(after.get(key) == value for key, value in patch.items()) and
            all(after.get(key) == before.get(key) for key in retained) and
            type(after.get('version')) is int and after['version'] > before['version'],
            'resource_update_mismatch', required=True)

    def agent_status(self, fixture, status):
        before = self.request('GET', fixture.agent_path)
        self.record('变更对象是本次智能体 ' + fixture.agent_id,
            before.get('id') == fixture.agent_id and before.get('workspace_id') == self.workspace and
            before.get('name') == fixture.agent_id, 'lifecycle_agent_identity_mismatch', required=True)
        self.request('PATCH', fixture.agent_path, {'status': status})
        after = self.request('GET', fixture.agent_path)
        self.record('智能体状态改变且保留历史定义 ' + status,
            after.get('status') == status and all(after.get(key) == before.get(key)
                for key in ('id', 'workspace_id', 'name', 'binding', 'description', 'runtime', 'model')),
            'agent_status_or_binding_mismatch', required=True)
        return after

    def reject(self, method, path, body, status, reason=None):
        self.last_exchange = None
        try:
            self.execution.client.product(method, self.execution.root + path, body)
            actual = 200
        except Failure as error:
            if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                raise
            actual = error.http_status
        exchange = self.last_exchange or {}
        response = exchange.get('response', {})
        try:
            response = json.loads(response['error_body']) if 'error_body' in response else response
        except (ValueError, TypeError):
            response = {}
        valid = actual == status and response.get('code') in {2, '2'}
        if reason:
            valid = valid and isinstance(response.get('details'), dict) and response['details'].get('reason') == reason
        self.record('操作按公开错误契约拒绝 ' + method + ' ' + path,
                    valid, 'lifecycle_rejection_mismatch:' + str(actual), required=True)

    def protected_delete(self):
        before = self.read_resource()
        self.reject('DELETE', self.primary_resource['path'], None, 422, self.kind.upper() + '_IN_USE')
        self.record('拒绝删除后资源完整保留', self.read_resource() == before,
                    'protected_delete_mutated_resource', required=True)

    def reject_reactivation(self):
        before = self.request('GET', self.primary.agent_path)
        self.reject('PATCH', self.primary.agent_path, {'status': 'active'}, 400)
        self.record('恢复被拒绝后仍归档且历史定义不变', before.get('status') == 'archived' and
            self.request('GET', self.primary.agent_path) == before,
            'rejected_reactivation_mutated_agent', required=True)

    def delete_resource(self):
        snapshots = {f.agent_path: self.request('GET', f.agent_path) for f in self.fixtures}
        self.request('DELETE', self.primary_resource['path'])
        self.record('删除后资源查询返回不存在', self.absent(self.primary_resource['path']),
                    'resource_delete_unconfirmed', required=True)
        self.record('资源删除没有改写归档智能体历史', all(
            self.request('GET', path) == saved and saved.get('status') == 'archived'
            for path, saved in snapshots.items()), 'resource_delete_changed_archived_agent', required=True)

    def evaluate(self, task_id, session_id, spec):
        self.session_id = session_id
        path = self.execution.evidence.root / 'events.jsonl'
        if spec not in {'primary', 'isolated'}:
            raise Failure('invalid_lifecycle_check')
        resource = self.peer_resource if spec == 'isolated' else self.primary_resource
        if self.kind == 'skill':
            check, _ = check_skill_call(path, task_id, resource['id'], session_id)
            self.checks.append(check)
            passed = check['passed']
        else:
            checks = evaluate_http_tool(path, task_id, session_id, resource['id'], 200, 'J2-07')
            self.checks.extend(checks)
            passed = all(check['passed'] for check in checks)
        if spec == 'isolated':
            self.record('失效资源没有成功执行或留下未结算调用', unavailable_resource_check(
                path, task_id, self.kind, self.primary_resource['id'], self.primary_resource['name']),
                'inactive_resource_executed_or_unsettled')
        # A verified baseline is required before mutating dependencies.
        if not passed:
            from .builder_flow import ProductCaseFailure
            raise ProductCaseFailure('lifecycle_resource_call_not_verified')

    def apply(self, action):
        allowed = {'isolation': {'deactivate', 'activate'}, 'shared': {'shared_references'},
                   'reactivation': {'disable_and_reactivate', 'delete_and_reactivate'}}
        if action not in allowed[self.mode] or action in self.state['actions']:
            raise Failure('invalid_lifecycle_action')
        if action in {'deactivate', 'activate'}:
            self.update_resource({'status': self.status if action == 'deactivate' else 'active'})
        elif action == 'shared_references':
            self.agent_status(self.primary, 'archived')
            for status in ('active', 'draft', 'disabled'):
                self.agent_status(self.peer, status)
                self.protected_delete()
            self.agent_status(self.peer, 'archived')
            self.update_resource({'description': 'Edited after all referencing Agents were archived.'})
            self.update_resource({'status': 'disabled'})
            self.update_resource({'status': 'active'})
            self.delete_resource()
        elif action == 'disable_and_reactivate':
            self.agent_status(self.primary, 'archived')
            self.update_resource({'status': 'disabled'})
            self.reject_reactivation()
            self.update_resource({'status': 'active'})
            self.agent_status(self.primary, 'active')
        elif action == 'delete_and_reactivate':
            self.agent_status(self.primary, 'archived')
            self.delete_resource()
            self.reject_reactivation()
        self.state['actions'].append(action)
        self.execution.checkpoint()

    def cleanup(self, *, dependencies_confirmed):
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = time.monotonic() + 90
        try:
            if not dependencies_confirmed:
                raise Failure('dependent_task_unconfirmed')
            # Archived references are history. Do not rewrite them, especially
            # after the case intentionally deleted their dependencies.
            for fixture in self.fixtures:
                errors_before = len(self.execution.report['harness_errors'])
                fixture.archive_owned_agent(agent_name=fixture.agent_id)
                if len(self.execution.report['harness_errors']) != errors_before:
                    raise Failure('lifecycle_candidate_cleanup_unconfirmed')
            for fixture in reversed(self.fixtures):
                resource = self.resource(fixture)
                owned = fixture.skill_pending if self.kind == 'skill' else resource['id'] in fixture.state['pending_ids']
                if not owned:
                    continue
                if not fixture.absent(resource['path']):
                    current = client.product('GET', self.execution.root + resource['path'])
                    if (current.get('id'), current.get('workspace_id'), current.get('name')) != (
                            resource['id'], self.workspace, resource['name']):
                        raise Failure('cleanup_resource_identity_mismatch')
                    client.product('DELETE', self.execution.root + resource['path'])
                    if not fixture.absent(resource['path']):
                        raise Failure('cleanup_resource_delete_unconfirmed')
                fixture.state['cleanup'] = 'completed'
            self.state['cleanup'] = 'completed'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_resource_lifecycle', error))
        finally:
            client.deadline = deadline
            client.observer = self.previous_observer
            self.execution.checkpoint()
