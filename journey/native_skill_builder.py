"""Verify Momo authors a real imported Skill Agent before its user confirms."""
import re
from urllib.parse import urlencode

from .builder_flow import AgentBuilderFlow, same_entries
from .native_skill_checks import observations
from .native_skill_resources import NativeSkillResources


class NativeSkillBuilderJourney(NativeSkillResources):
    def __init__(self, execution, checks):
        super().__init__(execution, checks, business_database=False)
        self.builder = AgentBuilderFlow(execution, self.session_id, checks, [self.agent_name])

    def prepare(self):
        variables = self.prepare_resources()
        self.employees_table = variables['employees_table']
        listing = self.request('GET', '/agents?' + urlencode({'query': self.agent_name, 'catalog': 'workspace'}))
        self.record('创建前不存在本次名称的智能体', listing.get('total') == 0 and listing.get('items') == [],
                    'native_builder_name_not_free', required=True)
        return {**variables, 'skill_id': self.skill_id, 'skill_name': self.name,
                'native_agent_name': self.agent_name, 'native_temp_dir': self.temp_dir}

    def check_execution(self, task_id, phase):
        start = len(self.checks)
        read, downloaded, scripts = observations(self.execution.evidence.root / 'events.jsonl', task_id, self.skill_id, self.name)
        self.record('本轮实际读取上传的业务 Skill', read, 'native_builder_skill_not_read:' + phase)
        expected_optional = ['english_name', 'hiring_date', 'is_active']
        valid = downloaded and any(item['succeeded'] and re.search(r'\bcheck_employees\b', item['command']) and
            any(obj.get('status') == 'validated' and obj.get('read_only') is True and
                obj.get('employees_table') == self.employees_table and
                same_entries(obj.get('missing_optional_columns'), expected_optional) for obj in item['objects']) for item in scripts)
        self.record('实际执行员工来源检查且返回正确的可选缺列', valid, 'native_builder_employee_check_missing:' + phase)
        current = self.data.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')
        tables = {next(iter(row.values())) for row in self.data.sql('employees', 'SHOW TABLES')}
        self.record('员工数据保持只读且未被用作业务库', current == self.data.state['employees_before'] and tables == {'employees'},
                    'native_builder_employee_source_changed:' + phase)
        for check in self.checks[start:]:
            check.update(session=phase.split(':')[0], phase=phase)

    def commit(self, task):
        self.builder.remember(task, self.agent_name)
        self.agent_id = self.builder.current['agent_id']
        self.state['agent_id'] = self.agent_id
        resource = self.builder.require_resource('skills', self.skill_id, workspace_id=self.workspace)
        # remember() proves absence before this authorized commit. Keep the exact
        # identity for cleanup even if the request commits but its response fails.
        self.agent_pending = True
        self.execution.checkpoint()
        self.builder.commit([resource])
        self.check_binding()
        current = self.request('GET', '/agent-builder/agents/' + self.agent_id + '/versions/' +
                               self.builder.created['version'] + '/current-agent')
        instruction = current.get('agent_md', '')
        self.record('自然语言生成的发布配置保存实际员工路径和临时目录',
                    isinstance(instruction, str) and self.employees_table in instruction and self.temp_dir in instruction,
                    'native_builder_configuration_missing', required=True)
        return self.builder.created
