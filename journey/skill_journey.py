"""Exercise custom Skill lifecycle through public APIs and a case-owned Agent."""
import time
import zipfile
from urllib.parse import urlencode

from .builder_flow import AgentBuilderFlow, ProductCaseFailure
from .client import Failure, error_record, segment
from .evidence import sha256


def same_skill_content(left, right):
    # Mutation responses carry subsecond timestamps; persisted API reads use
    # database precision. This case checks resource contents, not time precision.
    return {k: v for k, v in left.items() if k not in {'created_at', 'updated_at'}} == {
        k: v for k, v in right.items() if k not in {'created_at', 'updated_at'}}


class SkillJourney:
    def __init__(self, execution, definition, variables, checks):
        if (not isinstance(definition, dict) or set(definition) != {'mode', 'marker', 'initial_marker'} or
                definition['mode'] not in {'versions', 'disabled', 'package', 'bindings', 'manual'}):
            raise Failure('invalid_skill_journey')
        self.execution, self.checks = execution, checks
        self.session_id = execution.case['sessions'][0]['id']
        self.mode = definition['mode']
        self.marker = definition['marker'].format_map(variables)
        self.initial_marker = definition['initial_marker'].format_map(variables)
        if not self.marker or not self.initial_marker or self.marker == self.initial_marker:
            raise Failure('invalid_skill_journey_markers')
        self.workspace = execution.root.rsplit('/', 1)[-1]
        self.name = 'j2-lookup-' + execution.report['run_id'].replace('-', '')
        self.agent_id = 'j2-agent-' + execution.report['run_id'].replace('-', '')
        self.skill_id = 'j2-skill-' + execution.report['run_id'].replace('-', '')
        self.skill_pending = self.agent_pending = False
        self.builder = None
        self.state = {'mode': self.mode, 'name': self.name, 'cleanup': 'pending'}
        execution.report['skill_journey'] = self.state
        self.previous_observer = execution.client.observer
        execution.client.observer = self.observe

    def observe(self, exchange):
        if self.previous_observer:
            self.previous_observer(exchange)
        path = exchange.get('path', '')
        if path.startswith(tuple(self.execution.root + suffix for suffix in
                                 ('/skills', '/agents/' + self.agent_id, '/agent-builder/'))):
            self.execution.evidence.append('skill-http.jsonl', exchange)

    def record(self, rule, passed, reason, *, required=False):
        self.checks.append({'session': self.session_id, 'rule': rule, 'passed': bool(passed),
                            'detail': '' if passed else reason})
        self.execution.report['validation'] = {'checks': self.checks}
        self.execution.checkpoint()
        if required and not passed:
            raise ProductCaseFailure(reason)

    def request(self, method, path, body=None):
        try:
            result = self.execution.client.product(method, self.execution.root + path, body)
            self.execution.evidence.append('skill-operations.jsonl', {'method': method, 'path': path, 'request': body, 'response': result})
            return result
        except Failure as error:
            if error.code != 'product_error' and (error.code != 'http_error' or error.http_status in {None, 401, 403}):
                raise
            self.record('Skill 操作被产品接受', False, error.code + ':' + str(error.http_status))
            raise ProductCaseFailure('skill_operation_failed') from None

    def absent(self, path):
        try:
            self.execution.client.product('GET', self.execution.root + path)
        except Failure as error:
            if error.code == 'http_error' and error.http_status == 404:
                return True
            raise
        return False

    def rejected(self, method, path, body, expected):
        try:
            self.execution.client.product(method, self.execution.root + path, body)
        except Failure as error:
            if error.code == 'http_error' and error.http_status not in {None, 401, 403}:
                return error.http_status == expected
            raise
        return False

    @property
    def skill_path(self):
        return '/skills/' + segment(self.skill_id)

    @property
    def agent_path(self):
        return '/agents/' + segment(self.agent_id)

    def identity(self, item):
        return (isinstance(item, dict) and item.get('id') == self.skill_id and
                item.get('workspace_id') == self.workspace and item.get('name') == self.name)

    def instruction(self, marker):
        return 'The public project tracking code is ' + marker + '. Answer a request for the project tracking code with this exact code.'

    def prepare(self):
        if self.mode == 'package':
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
        if not self.absent(self.agent_path):
            raise Failure('skill_agent_id_exists')
        self.builder = AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id])
        resources = self.request('GET', '/agent-builder/resources')
        self.state['candidate_proposal_pending'] = True
        self.execution.checkpoint()
        candidate = self.request('POST', '/agent-builder/candidates', {
            'mode': 'create', 'conversation_id': 'manual-' + self.execution.report['run_id'],
            'raw_advice': 'Create a synthetic Skill verification Agent.', 'resources': resources,
            'candidate': {'agent_id': self.agent_id, 'name': self.agent_id,
                'description': 'Synthetic Skill verification', 'model_name': self.execution.model,
                'tool_names': [], 'skill_names': [self.name], 'knowledge_base_names': [], 'channel_bindings': [],
                'agent_md': 'Use the requested Skill to find the public project tracking code. '
                            'If the Skill is unavailable, reply SKILL_UNAVAILABLE. Never invent the code.',
                'change_reason': 'Synthetic Skill verification.'}})
        self.builder.remember_candidate(candidate, self.agent_id)
        self.state['candidate_proposal_pending'] = False
        self.state['candidate'] = {key: candidate[key] for key in ('agent_id', 'candidate_version', 'source_digest')}
        if candidate['agent_id'] != self.agent_id:
            raise ProductCaseFailure('manual_agent_id_mismatch')
        resource = self.builder.require_resource('skills', self.skill_id, workspace_id=self.workspace)
        self.agent_pending = True
        self.state['agent_id'] = self.agent_id
        self.execution.checkpoint()
        # Candidate publication carries resources in the compiled package. The
        # manual form also sends a root binding overlay; test that path separately.
        binding = {'skill_refs': [{'id': self.skill_id, 'kind': 'skill', 'workspace_id': self.workspace}]} \
            if self.mode == 'manual' else None
        self.builder.commit([resource], binding=binding)
        self.check_binding()
        if self.mode == 'disabled':
            self.set_status('disabled')
        if self.mode == 'bindings':
            self.check_binding_guards()
        return {'skill_id': self.skill_id, 'skill_name': self.name}

    def selector(self):
        if not self.agent_pending:
            raise Failure('skill_agent_not_created')
        return {'agent_id': self.agent_id, 'agent_workspace_id': self.workspace}

    def check_binding(self):
        detail = self.request('GET', self.agent_path + '/bindings')
        skills = detail.get('skills')
        matches = [item for item in skills if isinstance(item, dict) and item.get('id') == self.skill_id and
                   item.get('workspace_id') == self.workspace] if isinstance(skills, list) else []
        self.record('智能体绑定了本次创建的 Skill', detail.get('agent_id') == self.agent_id and
                    detail.get('agent_workspace_id') == self.workspace and len(matches) == 1,
                    'custom_skill_binding_mismatch', required=True)
        return detail

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

    def import_package(self):
        listing = self.request('GET', '/skills?' + urlencode({'query': self.name, 'catalog': 'workspace'}))
        if listing.get('total') != 0 or listing.get('items') != []:
            raise Failure('skill_package_name_exists')
        files = {'SKILL.md': '---\nname: ' + self.name + '\ndescription: Public project tracking lookup\n---\n\n' + self.instruction(self.marker),
                 'references/guide.md': 'Synthetic Journey-2 reference. No external service or credentials.'}
        archive = self.execution.work / 'skill-package.zip'
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for name, content in files.items():
                output.writestr(name, content)
        self.execution.evidence.write('skill-package-source.json', {'files': files, 'sha256': sha256(archive)})
        inspected = self.execution.client.upload_multipart(self.execution.root + '/skills/import/inspect', archive, expected_code=0)
        self.record('Skill 包预检解析名称和正文', inspected.get('name') == self.name and
                    inspected.get('instruction', {}).get('body') == self.instruction(self.marker),
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
            self.record('导入文件内容一致 ' + name, body == content.encode(), 'skill_package_content_mismatch')

    def cleanup(self, *, dependencies_confirmed):
        client, previous = self.execution.client, self.execution.client.deadline
        previous_errors = len(self.execution.report['harness_errors'])
        client.deadline = time.monotonic() + 30
        try:
            if not dependencies_confirmed:
                raise Failure('dependent_task_unconfirmed')
            if self.builder:
                self.builder.cleanup()
            if self.agent_pending and not self.absent(self.agent_path):
                agent = client.product('GET', self.execution.root + self.agent_path)
                if (agent.get('id') != self.agent_id or agent.get('workspace_id') != self.workspace or
                        agent.get('name') != self.agent_id):
                    raise Failure('cleanup_skill_agent_identity_mismatch')
                if agent.get('status') != 'archived':
                    client.product('PATCH', self.execution.root + self.agent_path, {'status': 'archived'})
                client.product('PATCH', self.execution.root + self.agent_path + '/bindings', {'binding': {'skill_refs': []}})
                bound = client.product('GET', self.execution.root + self.agent_path + '/bindings')
                # AgentBindingDetail.skills is omitted when empty (omitempty).
                skills = bound.get('skills', [])
                if not isinstance(skills, list) or any(item.get('id') == self.skill_id and
                        item.get('workspace_id') == self.workspace for item in skills):
                    raise Failure('cleanup_skill_unbind_unconfirmed')
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
