"""Import, bind and remove only a case-owned custom Skill."""
import hashlib
import time
import zipfile
from urllib.parse import urlencode

from .resource_journey import ResourceJourney
from .client import Failure, error_record, segment
from .evidence import sha256


class OwnedSkillJourney(ResourceJourney):
    kind = 'skill'

    def __init__(self, execution, checks, *, identity_suffix=''):
        self.execution, self.checks = execution, checks
        self.session_id = execution.case['sessions'][0]['id']
        self.workspace = execution.root.rsplit('/', 1)[-1]
        suffix = execution.report['run_id'].replace('-', '') + identity_suffix
        self.name, self.agent_id, self.skill_id = 'j2-lookup-' + suffix, 'j2-agent-' + suffix, 'j2-skill-' + suffix
        self.agent_name = self.agent_id
        self.skill_pending = self.agent_pending = False
        self.builder = None
        self.state = {'name': self.name, 'cleanup': 'pending'}
        execution.report['skill_journey' + identity_suffix] = self.state
        self.previous_observer = execution.client.observer
        execution.client.observer = self.observe

    @property
    def skill_path(self):
        return '/skills/' + segment(self.skill_id)

    def identity(self, item):
        return (isinstance(item, dict) and item.get('id') == self.skill_id and
                item.get('workspace_id') == self.workspace and item.get('name') == self.name)

    def check_binding(self):
        detail = self.request('GET', self.agent_path + '/bindings')
        skills = detail.get('skills')
        matches = [item for item in skills if isinstance(item, dict) and item.get('id') == self.skill_id and
                   item.get('workspace_id') == self.workspace] if isinstance(skills, list) else []
        self.record('智能体绑定了本次创建的 Skill', detail.get('agent_id') == self.agent_id and
                    detail.get('agent_workspace_id') == self.workspace and len(matches) == 1,
                    'custom_skill_binding_mismatch', required=True)
        return detail

    def import_files(self, files, instruction):
        archive = self.execution.work / 'skill-package.zip'
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for name, content in files.items():
                output.writestr(name, content)
        self.execution.evidence.write('skill-package-source.json', {'files': {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, 'sha256': sha256(archive)})
        inspected = self.execution.client.upload_multipart(self.execution.root + '/skills/import/inspect', archive, expected_code=0)
        self.record('Skill 包预检解析名称和正文', inspected.get('name') == self.name and
                    inspected.get('instruction', {}).get('body', '').strip() == instruction.strip(),
                    'skill_package_inspection_mismatch', required=True)
        self.skill_pending = True
        self.state['import_pending'] = True
        self.execution.checkpoint()
        imported = self.execution.client.upload_multipart(self.execution.root + '/skills/import', archive, expected_code=0)
        skill = imported.get('skill')
        if not isinstance(skill, dict) or not isinstance(skill.get('id'), str) or not skill['id']:
            self.record('Skill 导入返回有效身份', False, 'skill_import_identity_missing', required=True)
        self.skill_id = skill['id']
        self.state['skill_id'] = self.skill_id
        self.state['import_pending'] = False
        self.execution.checkpoint()
        self.record('导入的 Skill 可回读且身份正确', self.identity(skill) and self.identity(self.request('GET', self.skill_path)),
                    'skill_import_identity_mismatch', required=True)
        listing = self.request('GET', self.skill_path + '/files')
        items = listing.get('items')
        paths = {item.get('file_path') for item in items if isinstance(item, dict)} if isinstance(items, list) else set()
        self.record('导入包保留正文和引用文件', set(files).issubset(paths), 'skill_package_files_missing')
        for name, content in files.items():
            body = self.execution.client.request('GET', self.execution.root + self.skill_path + '/files/content?' +
                                                 urlencode({'path': name}), binary=True)
            self.record('导入文件内容一致 ' + name, body == content, 'skill_package_content_mismatch')

    def cleanup(self, *, dependencies_confirmed):
        client, previous = self.execution.client, self.execution.client.deadline
        previous_errors = len(self.execution.report['harness_errors'])
        client.deadline = time.monotonic() + 30
        try:
            if not dependencies_confirmed:
                raise Failure('dependent_task_unconfirmed')
            self.detach_agent_resources({self.skill_id}, agent_name=self.agent_name)
            if self.state.get('import_pending'):
                listing = client.product('GET', self.execution.root + '/skills?' + urlencode({'query': self.name, 'catalog': 'workspace'}))
                items = listing.get('items')
                if listing.get('total') != 1 or not isinstance(items, list) or len(items) != 1 or items[0].get('name') != self.name:
                    raise Failure('skill_import_outcome_unknown')
                self.skill_id = items[0]['id']
            if self.skill_pending and not self.absent(self.skill_path):
                skill = client.product('GET', self.execution.root + self.skill_path)
                if not self.identity(skill):
                    raise Failure('cleanup_skill_identity_mismatch')
                client.product('DELETE', self.execution.root + self.skill_path)
                if not self.absent(self.skill_path):
                    raise Failure('cleanup_skill_delete_unconfirmed')
            if self.state.get('candidate_proposal_pending') and not self.builder.seen:
                raise Failure('candidate_proposal_outcome_unconfirmed')
            self.state['cleanup'] = 'completed' if len(self.execution.report['harness_errors']) == previous_errors else 'incomplete'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_skill', error))
        finally:
            client.deadline = previous
            client.observer = self.previous_observer
            self.execution.checkpoint()

