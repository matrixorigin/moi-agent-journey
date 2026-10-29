"""Bind the requested system Skills before verifying each runtime load."""
from .owned_builder_journey import OwnedBuilderJourney
from .client import Failure


class SystemSkillJourney(OwnedBuilderJourney):
    kind = 'skill'

    def __init__(self, execution, skill_ids, checks):
        if (not isinstance(skill_ids, list) or not skill_ids or len(set(skill_ids)) != len(skill_ids) or
                any(not isinstance(item, str) or not item for item in skill_ids)):
            raise Failure('invalid_system_skill_case')
        super().__init__(execution, checks)
        self.skill_ids = skill_ids
        execution.report['system_skills'] = {'ids': skill_ids, 'agent_id': self.agent_id}

    def prepare(self):
        resources = self.request('GET', '/agent-builder/resources')
        skills = resources.get('skills', [])
        matches = [[s for s in skills if s.get('id') == key and s.get('workspace_id') == 'system'] for key in self.skill_ids]
        self.record('每个目标系统 Skill 都在可绑定清单中且身份唯一', all(len(items) == 1 for items in matches),
                    'system_skill_missing_or_ambiguous', required=True)
        self.publish_agent(names=[items[0]['name'] for items in matches], ids=self.skill_ids,
            resource_workspace='system', instruction='Follow the requested Skill loading checks. Load each requested bound Skill, '
            'do not execute its business instructions unless the user asks. Report each actual load result.')
