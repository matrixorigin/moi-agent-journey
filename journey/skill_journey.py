"""Exercise custom Skill lifecycle through public APIs and a case-owned Agent."""
import zipfile
from urllib.parse import urlencode

from .builder_flow import ProductCaseFailure
from .owned_skill_journey import OwnedSkillJourney
from .client import Failure
from .evidence import sha256


def same_skill_content(left, right):
    # Mutation responses carry subsecond timestamps; persisted API reads use
    # database precision. This case checks resource contents, not time precision.
    return {k: v for k, v in left.items() if k not in {'created_at', 'updated_at'}} == {
        k: v for k, v in right.items() if k not in {'created_at', 'updated_at'}}


class SkillJourney(OwnedSkillJourney):
    kind = 'skill'

    def __init__(self, execution, definition, variables, checks):
        if (not isinstance(definition, dict) or set(definition) != {'mode', 'marker', 'initial_marker'} or
                definition['mode'] not in {'versions', 'disabled', 'package', 'bindings', 'manual', 'invalid_package'}):
            raise Failure('invalid_skill_journey')
        super().__init__(execution, checks)
        self.mode = definition['mode']
        self.marker = definition['marker'].format_map(variables)
        self.initial_marker = definition['initial_marker'].format_map(variables)
        if not self.marker or not self.initial_marker or self.marker == self.initial_marker:
            raise Failure('invalid_skill_journey_markers')
        self.state['mode'] = self.mode

    def instruction(self, marker):
        return 'The public project tracking code is ' + marker + '. Answer a request for the project tracking code with this exact code.'

    def prepare(self):
        if self.mode in {'package', 'invalid_package'}:
            if self.mode == 'invalid_package':
                self.check_invalid_packages()
            self.import_package()
        else:
            if not self.absent(self.skill_path):
                raise Failure('skill_fixture_id_exists')
            self.skill_pending = True
            self.state['skill_id'] = self.skill_id
            self.execution.checkpoint()
            created = self.request('POST', '/skills', {
                'id': self.skill_id, 'name': self.name, 'status': 'active', 'source_type': 'custom',
                'description': 'Synthetic project tracking lookup ' + self.name,
                'routing_summary': {'summary': 'Read the public project tracking code.'},
                'instruction': {'body': self.instruction(self.initial_marker if self.mode == 'versions' else self.marker)},
                'tags': ['journey-2'], 'change_summary': 'initial'})
            self.record('自定义 Skill 创建后身份和初始版本正确', self.identity(created) and created.get('version') == 1,
                        'skill_create_mismatch', required=True)
            if self.mode == 'versions':
                self.check_versions(created)
        # The manual form sends a root binding overlay; test that path separately.
        binding = {'skill_refs': [{'id': self.skill_id, 'kind': 'skill', 'workspace_id': self.workspace}]} \
            if self.mode == 'manual' else None
        self.publish_agent(names=[self.name], ids=[self.skill_id], binding=binding,
            instruction='Use the requested Skill to find the public project tracking code. '
                        'If the Skill is unavailable, reply SKILL_UNAVAILABLE. Never invent the code.')
        self.check_binding()
        if self.mode == 'disabled':
            self.set_status('disabled')
        if self.mode == 'bindings':
            self.check_binding_guards()
        return {'skill_id': self.skill_id, 'skill_name': self.name}

    def set_status(self, status):
        before = self.request('GET', self.skill_path)
        self.request('PATCH', self.skill_path, {'status': status})
        after = self.request('GET', self.skill_path)
        self.record('Skill 状态设为 ' + status + ' 且内容保持不变', self.identity(after) and after.get('status') == status and
                    all(after.get(key) == before.get(key) for key in ('instruction', 'description', 'tags')),
                    'skill_status_change_mismatch', required=True)

    def check_versions(self, created):
        initial_instruction = created.get('instruction')
        changed = self.request('PATCH', self.skill_path, {'description': 'Edited ' + self.name, 'status': 'disabled',
                              'instruction': {'body': self.instruction(self.marker)}, 'change_summary': 'second'})
        self.record('修改内容生成第二版并可回读', self.identity(changed) and changed.get('version') == 2 and
                    changed.get('status') == 'disabled' and changed.get('instruction') == {'body': self.instruction(self.marker)} and
                    same_skill_content(self.request('GET', self.skill_path), changed), 'skill_update_mismatch', required=True)
        listed = self.request('GET', '/skills?' + urlencode({'query': self.name, 'status': 'disabled', 'catalog': 'workspace', 'limit': 1}))
        self.record('Skill 搜索和状态筛选定位本对象', listed.get('total') == 1 and
                    isinstance(listed.get('items'), list) and len(listed['items']) == 1 and self.identity(listed['items'][0]),
                    'skill_list_mismatch')
        history = self.request('GET', self.skill_path + '/versions')
        snapshots = self.snapshots(history)
        self.record('历史保留两版各自的内容', set(snapshots) == {1, 2} and
                    snapshots[1].get('instruction') == initial_instruction and
                    snapshots[2].get('instruction') == changed.get('instruction'), 'skill_history_mismatch', required=True)
        tagged = self.request('PATCH', self.skill_path, {'tags': ['journey-2', 'edited']})
        self.record('只修改标签不增加版本、不重写历史', tagged.get('version') == 2 and tagged.get('tags') == ['journey-2', 'edited'] and
                    self.request('GET', self.skill_path + '/versions') == history, 'skill_tags_rewrote_history')
        restored = self.request('POST', self.skill_path + '/versions/1/current', {'expected_current_version': 2})
        self.record('回退恢复第一版内容并保留当前标签', self.identity(restored) and restored.get('version') == 1 and
                    restored.get('status') == 'active' and restored.get('instruction') == initial_instruction and
                    restored.get('tags') == tagged.get('tags') and same_skill_content(self.request('GET', self.skill_path), restored),
                    'skill_restore_mismatch', required=True)
        for version, expected, status in ((2, 2, 409), (99, 1, 404)):
            rejected = self.rejected('POST', self.skill_path + '/versions/' + str(version) + '/current',
                                     {'expected_current_version': expected}, status)
            self.record('版本切换被正确拒绝 ' + str(status), rejected, 'skill_version_rejection_mismatch')
            self.record('拒绝切换后当前内容与历史不变 ' + str(status), same_skill_content(self.request('GET', self.skill_path), restored) and
                        self.request('GET', self.skill_path + '/versions') == history, 'skill_rejection_mutated_state')
        appended = self.request('PATCH', self.skill_path, {'instruction': {'body': self.instruction(self.marker)}})
        next_history = self.request('GET', self.skill_path + '/versions')
        next_snapshots = self.snapshots(next_history)
        self.record('回退后编辑追加第三版而不覆盖旧版本', appended.get('version') == 3 and
                    set(next_snapshots) == {1, 2, 3} and all(next_snapshots[v] == snapshots[v] for v in (1, 2)) and
                    next_snapshots[3].get('instruction') == {'body': self.instruction(self.marker)},
                    'skill_edit_after_restore_mismatch', required=True)

    def snapshots(self, history):
        items = history.get('items')
        if (not isinstance(items, list) or history.get('total') != len(items) or any(
                not isinstance(item, dict) or item.get('skill_id') != self.skill_id or type(item.get('version')) is not int or
                not isinstance(item.get('spec_snapshot'), dict) or item['spec_snapshot'].get('id') != self.skill_id or
                item['spec_snapshot'].get('version') != item['version'] for item in items) or
                len({item['version'] for item in items}) != len(items)):
            self.record('Skill 历史结构完整', False, 'invalid_skill_history', required=True)
        return {item['version']: item['spec_snapshot'] for item in items}

    def check_binding_guards(self):
        before = self.check_binding()
        ref = {'id': self.skill_id, 'kind': 'skill', 'workspace_id': self.workspace}
        self.request('PATCH', self.agent_path + '/bindings', {'binding_ops': [{'op': 'add', 'kind': 'skill', 'ref': ref}]})
        after_add = self.request('GET', self.agent_path + '/bindings')
        self.record('重复绑定不增加成员', {k: v for k, v in after_add.items() if k != 'agent_version'} ==
                    {k: v for k, v in before.items() if k != 'agent_version'},
                    'duplicate_skill_binding')
        before = after_add
        rejected = self.rejected('PATCH', self.agent_path + '/bindings', {'binding_ops': [
            {'op': 'remove', 'kind': 'skill', 'ref': ref},
            {'op': 'add', 'kind': 'skill', 'ref': {**ref, 'id': 'missing-' + self.skill_id}}]}, 400)
        self.record('包含不存在资源的绑定批次被拒绝', rejected, 'invalid_skill_batch_not_rejected')
        self.record('失败批次没有部分解绑', self.request('GET', self.agent_path + '/bindings') == before,
                    'invalid_skill_batch_mutated_binding')
        saved = self.request('GET', self.skill_path)
        self.record('已绑定的 Skill 不允许删除', self.rejected('DELETE', self.skill_path, None, 422),
                    'bound_skill_delete_not_rejected')
        self.record('删除拒绝后 Skill 和绑定保留', self.request('GET', self.skill_path) == saved and
                    self.request('GET', self.agent_path + '/bindings') == before, 'bound_skill_delete_mutated_state')

    def check_invalid_packages(self):
        listing = self.request('GET', '/skills?' + urlencode({'query': self.name, 'catalog': 'workspace'}))
        if listing.get('total') != 0 or listing.get('items') != []:
            raise Failure('skill_package_name_exists')
        self.execution.work.mkdir(parents=True, exist_ok=True)
        for defect in ('not-zip', 'missing-manifest'):
            archive = self.execution.work / (defect + '.zip')
            if defect == 'not-zip':
                archive.write_bytes(b'This is a synthetic invalid ZIP archive.')
            else:
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('README.md', 'Synthetic package ' + self.name + ' without SKILL.md')
            self.execution.evidence.write('invalid-skill-' + defect + '.json',
                                          {'defect': defect, 'sha256': sha256(archive)})
            for operation in ('inspect', 'import'):
                path = '/skills/import/inspect' if operation == 'inspect' else '/skills/import'
                if operation == 'import':
                    self.skill_pending = True
                    self.state['import_pending'] = True
                    self.execution.checkpoint()
                try:
                    imported = self.execution.client.upload_multipart(self.execution.root + path, archive, expected_code=0)
                    status = 'accepted'
                    skill = imported.get('skill')
                    if operation == 'import' and isinstance(skill, dict) and isinstance(skill.get('id'), str) and skill['id']:
                        self.skill_id = skill['id']
                        self.state['skill_id'] = self.skill_id
                        self.state['import_pending'] = False
                except Failure as error:
                    if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                        raise
                    status = error.http_status
                    if status == 400 and operation == 'import':
                        self.skill_pending = False
                        self.state['import_pending'] = False
                self.record('损坏 Skill 包被拒绝 ' + defect + ':' + operation, status == 400,
                            'invalid_skill_package_status:' + str(status), required=True)
            listing = self.request('GET', '/skills?' + urlencode({'query': self.name, 'catalog': 'workspace'}))
            self.record('拒绝后未创建 Skill ' + defect, listing.get('total') == 0 and listing.get('items') == [],
                        'invalid_skill_package_created_resource', required=True)

    def import_package(self):
        listing = self.request('GET', '/skills?' + urlencode({'query': self.name, 'catalog': 'workspace'}))
        if listing.get('total') != 0 or listing.get('items') != []:
            raise Failure('skill_package_name_exists')
        files = {'SKILL.md': '---\nname: ' + self.name + '\ndescription: Public project tracking lookup\n---\n\n' + self.instruction(self.marker),
                 'references/guide.md': 'Synthetic Journey-2 reference. No external service or credentials.'}
        self.import_files({name: content.encode() for name, content in files.items()}, self.instruction(self.marker))
