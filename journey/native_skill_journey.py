"""Upload the real upstream Skill, publish a bound Agent, and test natural requests."""
import json
import re
import time

from .channel_journey import resolve_values
from .client import Failure
from .native_skill_checks import (business_effect, confirmation_reply, csv_matches, observations, query_result_matches)
from .native_skill_stages import NativeSkillStages
from .native_skill_resources import NativeSkillResources


class NativeSkillJourney(NativeSkillResources):
    def __init__(self, execution, definition, checks):
        if (not isinstance(definition, dict) or set(definition) != {'stages', 'turn_timeout_seconds'} or
                type(definition['turn_timeout_seconds']) is not int or not 0 < definition['turn_timeout_seconds'] <= 300):
            raise Failure('invalid_native_skill_case')
        super().__init__(execution, checks, business_database=True)
        self.progress = NativeSkillStages(execution, definition['stages'])
        self.turn_timeout = definition['turn_timeout_seconds']
        self.discovered = self.downloaded = False
        self.before = None

    def prepare(self):
        variables = self.prepare_resources()
        agent_instruction = (
            '你是客户和事项进度管理助手，按已绑定能力处理用户的真实业务请求。\n'
            '以下配置已经为本智能体持久保存，不要重新分配或改用其他数据库：\n'
            'employees_table: ' + variables['employees_table'] + '\n'
            'sales_database: ' + variables['sales_database'] + '\n'
            '员工表已经验证可读取，作为只读共享来源；业务写入只允许落在上述 sales_database。'
            '按能力要求区分预览和用户后续确认，不擅自确认、修改脚本或直接 SQL 绕过规则。'
            '员工目录每天首次使用刷新，超过 24 小时或来源变化先刷新；事项查询实时关联员工表。\n'
            '所有下载、缓存、参数和生成的临时文件必须放在本次会话专用目录 ' + self.temp_dir +
            ' 下（可以先创建这个父目录，Skill 下载目标用其下不存在的新目录）。不要在其他路径创建持久文件。')
        self.publish_agent(names=[self.name], ids=[self.skill_id], instruction=agent_instruction)
        self.check_binding()
        current = self.request('GET', '/agent-builder/agents/' + self.agent_id + '/versions/' + self.builder.created['version'] + '/current-agent')
        saved = current.get('agent_md', '')
        self.record('创建确认后员工来源、业务库和临时目录持久保存',
            all(value in saved for value in (*variables.values(), self.temp_dir)), 'native_agent_configuration_missing', required=True)
        return {**variables, 'skill_id': self.skill_id, 'skill_name': self.name}

    def admit(self, turn):
        return self.progress.admit(turn, len(self.checks))

    def send(self, *args, **kwargs):
        self.before = self.data.snapshot()
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = min(deadline, time.monotonic() + self.turn_timeout)
        try:
            return self.execution.send(*args, **kwargs)
        finally:
            client.deadline = deadline

    def evaluate(self, task_id, conversation_id, session_id, phase, reply, definition, variables):
        spec = resolve_values(definition, variables)
        prior_checks_ok = all(check['passed'] for check in self.checks[self.progress.check_starts[phase]:])
        discovered, downloaded, scripts = observations(self.execution.evidence.root / 'events.jsonl', task_id, self.skill_id, self.name)
        self.discovered |= discovered
        self.downloaded |= downloaded
        missing_commands = [command for command in spec['commands'] if not any(item['succeeded'] and item['objects'] and
            re.search(r'\b' + re.escape(command) + r'\b', item['command']) for item in scripts)]
        script_ok = self.downloaded and not missing_commands
        self.record('自然业务请求使新智能体自行发现并成功读取绑定 Skill', self.discovered, 'native_skill_not_discovered')
        incomplete = self.downloaded and bool(missing_commands) and all(any(item['truncated'] and
            re.search(r'\b' + re.escape(command) + r'\b', item['command']) for item in scripts) for command in missing_commands)
        if incomplete:
            self.execution.report['failures'].append({'stage': phase, 'reason': 'native_tool_result_truncated'})
        self.record('下载实际绑定文件并执行所需脚本命令', script_ok,
                    'native_tool_result_truncated' if incomplete else 'native_packaged_script_not_executed')
        after = self.data.snapshot()
        employees_unchanged = after['employees'] == self.data.state['employees_before']
        self.record('共享员工数据保持只读', employees_unchanged, 'native_employee_source_mutated')
        valid, ids = business_effect(spec, self.before, after, variables)
        self.record('独立 SQL 回读符合本轮业务效果与未授权写入约束', valid, 'native_business_state_mismatch:' + phase)
        artifact_delivered = False
        query_ok = True
        if spec['kind'] in {'preview', 'settle'}:
            reply_ok = confirmation_reply(reply, ids, checklist=spec.get('checklist', False))
        elif spec['kind'] == 'initialize':
            reply_ok = all(value in reply for value in ('0007', '00018', '00042', '林青', '陈星', '周宁'))
        elif spec['kind'] == 'refusal':
            reply_ok = bool(re.search(r'不能|不允许|不合法|无法直接|不支持直接|先.*恢复', reply))
        elif spec['kind'] == 'query':
            query_ok = query_result_matches(scripts, spec['rows'])
            reply_ok = all(value in reply for value in spec.get('reply_contains', []))
            if spec.get('artifact'):
                self.execution.collect_artifacts(conversation_id)
                items = json.loads((self.execution.evidence.root / 'artifacts' / conversation_id / 'inventory.json').read_text())
                matches = [item for item in items if item.get('task_id') == task_id and item.get('file_name') == spec['artifact']]
                artifact_delivered = len(matches) == 1 and csv_matches(
                    (self.execution.evidence.root / matches[0]['local_path']).read_bytes(), spec['rows'])
                reply_ok &= artifact_delivered
        else:
            raise Failure('invalid_native_effect_kind')
        self.record('回复保留真实确认 ID 或交付与脚本结果一致', reply_ok and query_ok, 'native_reply_or_delivery_mismatch:' + phase)
        self.execution.evidence.write('native-state-' + phase + '.json',
            {'task_id': task_id, 'spec': spec, 'before': self.before, 'after': after, 'captured_change_ids': ids, 'scripts': scripts})
        if spec.get('refresh_employee'):
            self.data.update_employee()
        ready = valid and employees_unchanged and (spec['kind'] != 'preview' or all(value in reply for value in ids))
        evidence_only = incomplete and valid and employees_unchanged and self.discovered and reply_ok and prior_checks_ok
        self.progress.complete(phase, self.checks, ready=bool(ready), incomplete=bool(evidence_only))
        return artifact_delivered
