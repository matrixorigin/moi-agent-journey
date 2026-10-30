"""Repeated prompt-only edits must not accumulate installed custom Skills."""
import time
from urllib.parse import urlencode

from .agent_builder import candidate_matches
from .agent_package_journey import AgentPackageJourney
from .builder_flow import AgentBuilderFlow
from .client import Failure, error_record, segment


class EmbeddedSkillJourney(AgentPackageJourney):
    modes = {'embedded_skill'}

    def __init__(self, execution, definition, checks):
        super().__init__(execution, definition, checks)
        self.name = 'j2-embedded-' + execution.report['run_id'].replace('-', '')
        self.marker = 'PROJECT-' + execution.report['run_id'][:8]
        self.state['skill_name'] = self.name

    def owned_skills(self):
        result = self.request('GET', '/skills?' + urlencode({'query': self.name, 'catalog': 'workspace', 'limit': 100}))
        items = result.get('items')
        if not isinstance(items, list) or result.get('total') != len(items) or result.get('next_cursor'):
            raise Failure('embedded_skill_inventory_incomplete')
        for item in items:
            metadata = item.get('metadata') or {}
            if (item.get('workspace_id') != self.workspace or metadata.get('agent_id') != self.agent_id or
                    metadata.get('agent_package_custom_skill') is not True or
                    metadata.get('package_skill_name') != self.name):
                raise Failure('embedded_skill_owner_mismatch')
        return items

    def package_contents(self, files):
        if self.owned_skills():
            raise Failure('embedded_skill_already_exists')
        files['prompts/main.md'] = ('Use your bound Skill to answer questions about the public project code.').encode()
        files['custom/skills/' + self.name + '/skill.yaml'] = (
            'name: ' + self.name + '\ndescription: Synthetic project code lookup\ncategory: analysis\ntags: []\n'
            'routingSummary:\n  summary: Look up the public project code\n  examples: []\ninstruction:\n'
            '  body: The public project code is ' + self.marker + '. Return it when asked.\n'
            '  variablesSchema: {}\nrequirements:\n  toolRefs: []\n  toolResourceRefs: []\n'
            '  knowledgeBaseRoles: []\n  fileInputs: []\n  modelCapabilities: []\n'
            'parametersSchema: {}\noutputContract: {}\n').encode()
        return files

    def prepare(self):
        super().prepare()
        initial = self.owned_skills()
        self.record('包内 Skill 仅安装一次', len(initial) == 1, 'embedded_skill_initial_count', required=True)
        self.builder = AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id])
        self.builder.owned_published.add(self.agent_id)
        version = '1.0.0'
        for revision in (1, 2):
            path = '/agent-builder/agents/' + segment(self.agent_id) + '/versions/' + segment(version) + '/current-agent'
            current = self.request('GET', path)
            instruction = 'Use your bound Skill to answer the public project code. Prompt revision ' + str(revision) + '.'
            self.state['candidate_proposal_pending'] = True
            self.execution.checkpoint()
            candidate = self.request('POST', '/agent-builder/candidates', {
                'mode': 'revise', 'conversation_id': 'manual-' + self.execution.report['run_id'],
                'source_agent_id': self.agent_id, 'source_agent_workspace_id': self.workspace,
                'source_version': version, 'raw_advice': 'Change only the system prompt.',
                'resources': self.request('GET', '/agent-builder/resources'),
                'changes': {'base_revision': current['base_revision'], 'change_reason': 'Prompt-only regression',
                            'agent_md': instruction}})
            identity = (candidate_matches(candidate, self.agent_id, self.workspace, mode='revise') and
                        candidate.get('agent_id') == self.agent_id)
            if identity:
                self.state['candidate_proposal_pending'] = False
                self.builder.revisions[self.agent_id] = (candidate, self.agent_id)
            self.record('提示词修订保留智能体身份并生成有效候选 ' + str(revision), identity and candidate.get('status') == 'valid',
                'embedded_skill_revision_invalid', required=True)
            self.builder.current = candidate
            self.builder.commit()
            version = self.builder.created['version']
            updated = self.request('GET', '/agent-builder/agents/' + segment(self.agent_id) + '/versions/' + segment(version) + '/current-agent')
            self.record('仅修改提示词且其他可编辑配置保留 ' + str(revision),
                updated.get('agent_md') == instruction and all(updated.get(key) == current.get(key) for key in
                    ('name', 'description', 'model_name', 'tool_names', 'knowledge_base_names', 'catalog_files', 'channel_bindings')),
                'prompt_revision_changed_configuration')
            skills = self.owned_skills()
            self.execution.evidence.write('embedded-skills/revision-' + str(revision) + '.json', skills)
            self.record('提示词修改不累积重复的已安装 Skill ' + str(revision),
                        len([item for item in skills if item.get('status') != 'archived']) == 1,
                        'prompt_revision_duplicates_installed_skill')
            binding = self.request('GET', self.agent_path + '/bindings')
            refs = [ref for ref in binding.get('skills', []) if ref.get('binding_source') == 'agent_package' and
                    ref.get('workspace_id') == self.workspace]
            self.record('当前绑定只有一个有效包内 Skill ' + str(revision), len(refs) == 1 and
                refs[0].get('status') == 'active' and any(item.get('id') == refs[0].get('id') and
                    item.get('instruction') == initial[0].get('instruction') for item in skills),
                'embedded_skill_binding_corrupted', required=True)
            self.state['current_skill_id'] = refs[0]['id']
        return {'embedded_skill_id': self.state['current_skill_id'], 'embedded_marker': self.marker}

    def cleanup(self, *, dependencies_confirmed):
        before = len(self.execution.report['harness_errors'])
        if self.state.get('candidate_proposal_pending'):
            self.execution.report['harness_errors'].append({'stage': 'cleanup_embedded_skill',
                                                            'reason': 'candidate_proposal_outcome_unconfirmed'})
            dependencies_confirmed = False
        if dependencies_confirmed and self.builder:
            self.builder.cleanup()
            dependencies_confirmed = len(self.execution.report['harness_errors']) == before
        super().cleanup(dependencies_confirmed=dependencies_confirmed)
        if self.state['cleanup'] != 'completed':
            return
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = time.monotonic() + 30
        try:
            for skill in self.owned_skills():
                path = '/skills/' + segment(skill['id'])
                self.request('DELETE', path)
                if not self.absent(path):
                    raise Failure('embedded_skill_delete_unconfirmed')
            self.state['skills_cleanup'] = 'completed'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_embedded_skill', error))
        finally:
            client.deadline = deadline
            self.execution.checkpoint()
