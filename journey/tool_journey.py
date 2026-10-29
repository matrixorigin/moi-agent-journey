"""Create real HTTP tools, publish an Agent and verify its task-bound calls."""
import json
import time
from urllib.parse import urlencode

from .client import Failure, error_record, segment
from .resource_journey import ResourceJourney

FIXTURE_REVISION = 'caf0dbe7c549b71fa09e3eb3d201adf10059aa92'
FIXTURE_ROOT = 'https://raw.githubusercontent.com/matrixorigin/moi-agent-journey/' + FIXTURE_REVISION + '/'


class ToolJourney(ResourceJourney):
    kind = 'tool'

    def __init__(self, execution, definition, checks):
        if not isinstance(definition, dict) or set(definition) != {'mode'} or definition['mode'] not in {'lifecycle', 'http_error', 'bindings', 'invalid', 'arguments'}:
            raise Failure('invalid_tool_journey')
        self.execution, self.checks = execution, checks
        self.session_id = execution.case['sessions'][0]['id']
        self.mode = definition['mode']
        self.workspace = execution.root.rsplit('/', 1)[-1]
        suffix = execution.report['run_id'].replace('-', '')
        self.agent_id = 'j2-agent-' + suffix
        self.agent_pending, self.builder = False, None
        self.tools = [{'id': 'j2-http-' + suffix, 'name': 'journey_lookup_' + suffix,
                       'uri': FIXTURE_ROOT + 'cases/journey-2/J2-07/case.json'}]
        if self.mode == 'http_error':
            self.tools.append({'id': 'j2-missing-' + suffix, 'name': 'journey_missing_' + suffix,
                               'uri': FIXTURE_ROOT + 'missing-' + suffix + '.json'})
        self.state = {'mode': self.mode, 'tools': self.tools, 'pending_ids': [], 'cleanup': 'pending',
                      'fixture_revision': FIXTURE_REVISION}
        execution.report['tool_journey'] = self.state
        self.previous_observer = execution.client.observer
        execution.client.observer = self.observe

    def path(self, tool):
        return '/tools/' + segment(tool['id'])

    def identity(self, value, tool):
        return (isinstance(value, dict) and value.get('id') == tool['id'] and
                value.get('workspace_id') == self.workspace and value.get('name') == tool['name'])

    def prepare(self):
        for tool in self.tools:
            if not self.absent(self.path(tool)):
                raise Failure('tool_fixture_id_exists')
            self.state['pending_ids'].append(tool['id'])
            self.execution.checkpoint()
            definition = {
                'id': tool['id'], 'name': tool['name'], 'kind': 'http_api', 'status': 'active',
                'description': 'Fetch the public synthetic journey case fixture via HTTP GET.',
                'category': 'retrieval', 'side_effect_class': 'read', 'tags': ['journey-2'],
                'source_ref': {'type': 'http_api', 'uri': tool['uri'],
                               'config': {'method': 'GET', 'timeout_seconds': 20}},
                'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                'output_schema': {'type': 'object'}}
            if self.mode == 'invalid':
                for field in ('kind', 'side_effect_class'):
                    self.record('工具定义拒绝非法字段 ' + field,
                        self.rejected('POST', '/tools', {**definition, field: 'invalid-value'}, 400),
                        'invalid_tool_definition_accepted')
                    self.record('拒绝后没有创建工具 ' + field, self.absent(self.path(tool)),
                                'invalid_tool_definition_created', required=True)
            created = self.request('POST', '/tools', definition)
            self.record('自定义工具创建后可回读且可绑定 ' + tool['id'],
                self.identity(created, tool) and created.get('version') == 1 and created.get('bindable') is True and
                self.identity(self.request('GET', self.path(tool)), tool), 'tool_create_mismatch', required=True)
        if self.mode == 'lifecycle':
            self.check_lifecycle()
        self.publish_agent(names=[tool['name'] for tool in self.tools], ids=[tool['id'] for tool in self.tools],
            instruction='Call the requested HTTP tool to read its result. Never invent a tool result or substitute web search. '
                        'HTTP 404 means the resource is missing; report it accurately. Return JSON when requested.')
        bound = self.request('GET', self.agent_path + '/bindings')
        items = bound.get('tools', [])
        self.record('发布后的绑定包含全部本次工具',
            bound.get('agent_id') == self.agent_id and bound.get('agent_workspace_id') == self.workspace and
            isinstance(items, list) and all(sum(isinstance(item, dict) and item.get('id') == tool['id'] and
                item.get('workspace_id') == self.workspace for item in items) == 1 for tool in self.tools),
            'tool_binding_mismatch', required=True)
        if self.mode == 'bindings':
            self.check_binding_guards(bound)
        return {'tool_name': self.tools[0]['name'], 'tool_id': self.tools[0]['id'],
                **({'missing_tool_name': self.tools[1]['name'], 'missing_tool_id': self.tools[1]['id']}
                   if len(self.tools) == 2 else {})}

    def check_lifecycle(self):
        tool = self.tools[0]
        changed = self.request('PATCH', self.path(tool), {'description': 'Updated public fixture lookup', 'tags': ['journey-2', 'edited']})
        readback = self.request('GET', self.path(tool))
        self.record('修改描述和标签持久化并递增版本', self.identity(changed, tool) and changed.get('version') == 2 and
            readback.get('description') == 'Updated public fixture lookup' and readback.get('tags') == ['journey-2', 'edited'] and
            readback.get('version') == 2, 'tool_update_mismatch')
        before = readback
        for status in ('disabled', 'active'):
            changed = self.request('PATCH', self.path(tool), {'status': status})
            current = self.request('GET', self.path(tool))
            self.record('工具状态和可绑定标记一致 ' + status, self.identity(current, tool) and current.get('status') == status and
                current.get('bindable') is (status == 'active') and changed.get('status') == status and all(
                    current.get(key) == before.get(key) for key in ('source_ref', 'input_schema', 'output_schema', 'description', 'tags')),
                'tool_status_or_content_mismatch', required=True)
            listing = self.request('GET', '/tools?' + urlencode({'query': tool['name'], 'status': status, 'catalog': 'workspace'}))
            self.record('工具名称和状态筛选定位本对象 ' + status, listing.get('total') == 1 and
                isinstance(listing.get('items'), list) and len(listing['items']) == 1 and self.identity(listing['items'][0], tool),
                'tool_list_mismatch')

    def check_binding_guards(self, before):
        tool = self.tools[0]
        ref = {'id': tool['id'], 'kind': 'tool', 'workspace_id': self.workspace}
        self.request('PATCH', self.agent_path + '/bindings', {'binding_ops': [{'op': 'add', 'kind': 'tool', 'ref': ref}]})
        after = self.request('GET', self.agent_path + '/bindings')
        self.record('重复绑定不增加工具成员', {k: v for k, v in before.items() if k != 'agent_version'} ==
            {k: v for k, v in after.items() if k != 'agent_version'}, 'duplicate_tool_binding')
        rejected = self.rejected('PATCH', self.agent_path + '/bindings', {'binding_ops': [
            {'op': 'remove', 'kind': 'tool', 'ref': ref},
            {'op': 'add', 'kind': 'tool', 'ref': {**ref, 'id': 'missing-' + tool['id']}}]}, 400)
        self.record('不存在工具的绑定批次被拒绝', rejected, 'invalid_tool_batch_not_rejected')
        self.record('拒绝批次没有部分解绑', self.request('GET', self.agent_path + '/bindings') == after,
                    'invalid_tool_batch_mutated_binding')
        saved = self.request('GET', self.path(tool))
        self.record('已绑定工具不能删除', self.rejected('DELETE', self.path(tool), None, 422), 'bound_tool_delete_not_rejected')
        self.record('拒绝删除后工具和绑定保留', self.request('GET', self.path(tool)) == saved and
            self.request('GET', self.agent_path + '/bindings') == after, 'bound_tool_delete_mutated_state')

    def cleanup(self, *, dependencies_confirmed):
        client, previous = self.execution.client, self.execution.client.deadline
        previous_errors = len(self.execution.report['harness_errors'])
        client.deadline = time.monotonic() + 30
        try:
            if not dependencies_confirmed:
                raise Failure('dependent_task_unconfirmed')
            self.detach_agent_resources(set(self.state['pending_ids']))
            for tool in self.tools:
                if tool['id'] not in self.state['pending_ids'] or self.absent(self.path(tool)):
                    continue
                if not self.identity(client.product('GET', self.execution.root + self.path(tool)), tool):
                    raise Failure('cleanup_tool_identity_mismatch')
                client.product('DELETE', self.execution.root + self.path(tool))
                if not self.absent(self.path(tool)):
                    raise Failure('cleanup_tool_delete_unconfirmed')
            if self.state.get('candidate_proposal_pending') and not self.builder.seen:
                raise Failure('candidate_proposal_outcome_unconfirmed')
            self.state['cleanup'] = 'completed' if len(self.execution.report['harness_errors']) == previous_errors else 'incomplete'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_tool', error))
        finally:
            client.deadline = previous
            client.observer = self.previous_observer
            self.execution.checkpoint()


def evaluate_http_tool(path, task_id, session_id, tool_id, status, body_id=None):
    """Require a real matching call/result pair; a plausible answer alone fails."""
    if not isinstance(tool_id, str) or not tool_id or type(status) is not int:
        raise Failure('invalid_http_tool_case')
    calls, results = set(), []
    for line in path.read_text().splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict) or payload.get('tool_id') != tool_id:
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call' and payload.get('arguments') == {}:
            calls.add(call_id)
        elif event.get('kind') == 'tool_result' and call_id in calls:
            wrapper = payload.get('output')
            output = wrapper.get('output') if isinstance(wrapper, dict) else None
            metadata = wrapper.get('metadata') if isinstance(wrapper, dict) else None
            if (payload.get('success') is True and isinstance(output, dict) and isinstance(metadata, dict) and
                    metadata.get('source') == 'http_api'):
                results.append((call_id, output))
    matching = [(call_id, item) for call_id, item in results
                if type(item.get('status_code')) is int and item['status_code'] == status]
    content = body_id is None
    if body_id is not None:
        for _, item in matching:
            try:
                body = json.loads(item.get('body', ''))
            except (ValueError, TypeError):
                continue
            content |= isinstance(body, dict) and body.get('id') == body_id
    checks = [
        {'session': session_id, 'rule': '本任务实际调用指定 HTTP 工具且参数正确', 'passed': bool(calls),
         'detail': '' if calls else 'http_tool_call_missing'},
        {'session': session_id, 'rule': '对应工具结果包含预期 HTTP 状态和内容', 'passed': bool(matching) and content,
         'detail': '' if matching and content else 'http_tool_result_mismatch'},
    ]
    if 400 <= status <= 599 and matching:
        checks[-1]['expected_tool_errors'] = [
            {'task_id': task_id, 'call_id': call_id, 'kind': 'http_response_failure', 'code': str(status)}
            for call_id, _ in matching]
    return checks
