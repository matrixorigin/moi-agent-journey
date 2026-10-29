"""Explicit dedicated Channel bindings and real provider receipts for outbound tests."""
import json
import os
import re

from .builder_flow import ProductCaseFailure
from .client import Failure
from .owned_builder_journey import OwnedBuilderJourney

PROVIDERS = {
    'github': ('github', 'github_app', 'github', {'owner', 'repo'}),
    'feishu': ('feishu', 'feishu_app', 'feishu', {'receive_id', 'receive_id_type'}),
    'slack': ('slack', 'slack_app', 'slack', {'channel_id'}),
    'wecom': ('wecom', 'wecom_app', 'wecom', {'userid'}),
    'qq_mail': ('qq_mail', 'qq_mail', 'qq-mail', {'email'}),
    'wecom_mail': ('wecom', 'wecom_mail', 'wecom-mail', {'email'}),
}


class ChannelUnavailable(Exception):
    pass


def channel_targets(execution, providers, checks):
    """Inspect all prerequisites before any agent publication or external write."""
    if not isinstance(providers, list) or not providers or len(set(providers)) != len(providers) or any(p not in PROVIDERS for p in providers):
        raise Failure('invalid_channel_case')
    try:
        configured = json.loads(os.environ.get('MOI_J2_CHANNEL_TARGETS', '{}'))
    except ValueError:
        raise Failure('invalid_channel_targets') from None
    if not isinstance(configured, dict) or set(configured) - set(PROVIDERS):
        raise Failure('invalid_channel_targets')
    ready, missing = {}, []
    for name in providers:
        provider, kind, _, keys = PROVIDERS[name]
        page = execution.client.product('GET', execution.root + '/channels/' + provider + '/instances')
        items = page.get('items')
        if not isinstance(items, list):
            raise Failure('invalid_channel_inventory')
        target = configured.get(name)
        reason = None
        if target is None:
            reason = 'dedicated_target_not_configured:' + name
        elif (not isinstance(target, dict) or set(target) != keys | {'instance_id'} or
              any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in target.values())):
            raise Failure('invalid_channel_target:' + name)
        else:
            matches = [item for item in items if item.get('id') == target['instance_id'] and
                       item.get('workspace_id') == execution.root.rsplit('/', 1)[-1] and
                       item.get('provider') == provider and item.get('channel_type') == kind and item.get('status') == 'active']
            if len(matches) != 1:
                reason = 'dedicated_channel_unavailable:' + name
            elif name == 'feishu' and target['receive_id_type'] != 'chat_id':
                raise Failure('feishu_test_target_must_be_chat_id')
            elif name == 'wecom' and (target['userid'] == '@all' or '|' in target['userid']):
                raise Failure('wecom_test_target_must_be_single_user')
            elif name in {'qq_mail', 'wecom_mail'} and matches[0].get('config', {}).get('email') != target['email']:
                reason = 'dedicated_mailbox_identity_mismatch:' + name
            elif name == 'slack' and not os.environ.get('MOI_J2_SLACK_READ_TOKEN'):
                reason = 'receiver_read_credential_missing:slack'
            elif name == 'feishu' and not all(os.environ.get(key) for key in ('MOI_J2_FEISHU_APP_ID', 'MOI_J2_FEISHU_APP_SECRET')):
                reason = 'receiver_read_credential_missing:feishu'
            else:
                ready[name] = target
        evidence = {'provider': name, 'target': target, 'ready': reason is None, 'reason': reason,
                    'instances': [{key: item.get(key) for key in ('id','workspace_id','provider','channel_type','status')} for item in items]}
        execution.evidence.append('channel-preflight.jsonl', evidence)
        execution.report.setdefault('channel_preflight', []).append(evidence)
        checks.append({'session': name, 'rule': '专用连接、接收目标和验收凭据可用', 'passed': reason is None, 'detail': reason or ''})
        if reason:
            missing.append(reason)
    execution.report['validation'] = {'checks': checks}
    execution.checkpoint()
    if missing:
        raise ChannelUnavailable(';'.join(missing))
    return ready


class ChannelJourney(OwnedBuilderJourney):
    kind = 'skill'

    def __init__(self, execution, name, target, checks):
        super().__init__(execution, checks)
        self.session_id, self.name, self.target = name, name, target
        self.agent_id += '-' + name.replace('_', '-')
        self.provider, self.channel_type, skill, _ = PROVIDERS[name]
        self.skill_id = 'moi.agent.momo.skill.' + skill
        execution.report.setdefault('channel_agents', {})[name] = self.state

    def candidate_request(self, names, instruction, resources):
        body = super().candidate_request(names, instruction, resources)
        body['candidate']['channel_bindings'] = [{'skill_name': names[0], 'skill_workspace_id': 'system',
            'provider': self.provider, 'workspace_id': self.workspace, 'instance_id': self.target['instance_id']}]
        return body

    def prepare(self):
        resources = self.request('GET', '/agent-builder/resources')
        matches = [item for item in resources.get('skills', []) if item.get('id') == self.skill_id and item.get('workspace_id') == 'system']
        if len(matches) != 1:
            self.record('所需集成 Skill 可绑定', False, 'channel_skill_missing', required=True)
        self.publish_agent(names=[matches[0]['name']], ids=[self.skill_id], resource_workspace='system',
            instruction='Use only the explicitly requested dedicated test target and bound integration. '
            'Follow each tool name and arguments exactly. Never retry a write, change recipients, or use another integration. '
            'All writes requested in this test are authorized for these dedicated resources. Report actual results.')
        bindings = self.builder.current.get('display', {}).get('channel_bindings', [])
        self.record('候选明确绑定本次指定 Channel', len(bindings) == 1 and
            bindings[0].get('instance_id') == self.target['instance_id'] and bindings[0].get('workspace_id') == self.workspace and
            bindings[0].get('skill_name') == matches[0]['name'], 'channel_binding_identity_mismatch', required=True)
        return {self.name + '_' + key: value for key, value in self.target.items() if key != 'instance_id'}


def resolve_values(value, variables):
    if isinstance(value, dict):
        return {k: resolve_values(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_values(v, variables) for v in value]
    if isinstance(value, str):
        match = re.fullmatch(r'\{([a-z][a-z0-9_]*)\}', value)
        return variables[match[1]] if match else value.format_map(variables)
    return value


def field(value, path):
    for part in path.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def subset(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and subset(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(subset(a,b) for a,b in zip(actual,expected))
    return type(actual) is type(expected) and actual == expected


def evaluate_channel_call(execution, task_id, session_id, definition, variables, checks):
    """Join exact arguments to a single real result; capture only verified server IDs."""
    config = resolve_values(definition, variables)
    calls, outputs = {}, {}
    for line in (execution.evidence.root / 'events.jsonl').read_text().splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict) or payload.get('tool_id') != config['tool']:
            continue
        call_id = payload.get('call_id')
        if event.get('kind') == 'tool_call' and isinstance(call_id, str):
            calls[call_id] = payload.get('arguments')
        elif event.get('kind') == 'tool_result' and call_id in calls:
            outputs[call_id] = payload
    matches = [call for call,args in calls.items() if isinstance(args, dict) and
               set(args) == set(config['arguments']) and subset(args, config['arguments'])]
    call_ok = len(calls) == len(matches) == 1
    payload = outputs.get(matches[0], {}) if call_ok else {}
    wrapper = payload.get('output')
    output = wrapper.get('output') if isinstance(wrapper, dict) else None
    result_ok = payload.get('success') is True and isinstance(output, dict) and all(
        subset(field(output, path), value) for path,value in config.get('result_fields', {}).items())
    for path in config.get('empty_fields', []):
        # Provider omits or returns an empty string for recipient errors on success.
        result_ok = result_ok and field(output, path) in (None, '')
    for path, value in config.get('contains_fields', {}).items():
        actual = field(output, path)
        result_ok = result_ok and isinstance(actual, str) and value in actual
    if config.get('expected_error'):
        result_ok = payload.get('success') is False and bool(re.search(config['expected_error'], json.dumps(wrapper), re.I))
    captured = {key: field(output, path) for key,path in config.get('capture', {}).items()}
    result_ok = result_ok and all(type(value) in {str,int} and bool(value) for value in captured.values())
    for label, passed, reason in [('只调用一次指定工具且目标和参数正确', call_ok, 'channel_call_mismatch'),
                                  ('供应商返回可核验的结果和身份', result_ok, 'channel_result_mismatch')]:
        checks.append({'session':session_id,'rule':label + ' ' + config['tool'], 'passed':bool(passed),'detail':'' if passed else reason})
    execution.report['validation']={'checks':checks}
    execution.checkpoint()
    if not call_ok or not result_ok:
        raise ProductCaseFailure('channel_operation_not_verified')
    variables.update(captured)
    execution.evidence.append('channel-receipts.jsonl', {'task_id':task_id,'tool_id':config['tool'],
        'call_id':matches[0],'arguments':calls[matches[0]],'output':output,'captured':captured})
    return output
