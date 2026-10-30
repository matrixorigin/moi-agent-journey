"""Check real Agent Builder candidates, publication and identity-bound cleanup."""
import json
import time
from urllib.parse import urlencode

from .agent_builder import candidate_from_task, candidate_output_from_task, candidate_outputs, candidate_matches, cleanup_candidate
from .client import Failure, error_record, segment


class ProductCaseFailure(Exception):
    """A product prerequisite failed; later dependent actions cannot be sent."""


def resource_workspace(reference, current_workspace):
    # Manual Builder/package refs omit workspace_id for a local resource;
    # runtime-authoring views materialize it. Both are defined product forms.
    return reference.get('workspace_id', current_workspace)


def same_entries(before, after):
    return (isinstance(before, list) and isinstance(after, list) and
            sorted(json.dumps(item, sort_keys=True) for item in before) ==
            sorted(json.dumps(item, sort_keys=True) for item in after))


def same_resource_transition(before_names, after_names, before_refs, after_refs, changes, workspace_id):
    if not all(isinstance(value, list) for value in (before_names, after_names, before_refs, after_refs)):
        return False
    if any(not isinstance(item, dict) for item in before_refs + after_refs):
        return False
    before_refs, after_refs = list(before_refs), list(after_refs)
    before_names, after_names = list(before_names), list(after_names)
    for operation in ('remove', 'add'):
        for resource_id in changes.get(operation, []):
            source, destination = (before_refs, after_refs) if operation == 'remove' else (after_refs, before_refs)
            source_names = before_names if operation == 'remove' else after_names
            matches = [item for item in source if item.get('id') == resource_id and item.get('workspace_id') == workspace_id]
            if (len(matches) != 1 or any(item.get('id') == resource_id and item.get('workspace_id') == workspace_id
                                         for item in destination) or
                    not isinstance(matches[0].get('name'), str) or matches[0]['name'] not in source_names):
                return False
            source.remove(matches[0])
            source_names.remove(matches[0]['name'])
    return same_entries(before_refs, after_refs) and same_entries(before_names, after_names)


def same_configuration(old, updated, changes=None, *, workspace_id=None):
    old_display, new_display = old.get('display', {}), updated.get('display', {})
    old_refs, new_refs = old.get('resolved_refs'), updated.get('resolved_refs')
    if (old_display.get('model') != new_display.get('model') or
            not isinstance(old_refs, dict) or not isinstance(new_refs, dict)):
        return False
    for field in ('skills', 'tools', 'knowledge_bases', 'channel_bindings'):
        if changes and field in changes:
            expected_workspace = workspace_id if field == 'knowledge_bases' else 'system'
            if not expected_workspace:
                return False
            if not same_resource_transition(old_display.get(field), new_display.get(field),
                                            old_refs.get(field, []), new_refs.get(field, []), changes[field],
                                            expected_workspace):
                return False
            continue
        pairs = ((old_display.get(field), new_display.get(field)),
                 (old_refs.get(field, []), new_refs.get(field, [])))
        if any(not same_entries(before, after) for before, after in pairs):
            return False
    return True


def same_published_config(old, updated, changes=None, *, keep_description=False, allow_instruction_change=False,
                          workspace_id=None):
    for key in ('agent_id', 'name', 'model_name'):
        if not isinstance(old.get(key), str) or not old[key] or old[key] != updated.get(key):
            return False
    if (not isinstance(old.get('agent_md'), str) or not isinstance(updated.get('agent_md'), str) or
            (not allow_instruction_change and old['agent_md'] != updated['agent_md']) or
            old.get('model_config_ref') != updated.get('model_config_ref')):
        return False
    if keep_description and (not isinstance(old.get('description'), str) or old['description'] != updated.get('description')):
        return False
    names = {'skills': 'skill_names', 'tools': 'tool_names', 'knowledge_bases': 'knowledge_base_names'}
    for key in ('tool_names', 'skill_names', 'knowledge_base_names', 'catalog_files', 'channel_bindings'):
        if changes and key in {names[field] for field in changes}:
            continue
        if not same_entries(old.get(key), updated.get(key)):
            return False
    before, after = old.get('resource_refs'), updated.get('resource_refs')
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    for key in set(before) | set(after):
        if changes and key in changes:
            expected_workspace = workspace_id if key == 'knowledge_bases' else 'system'
            if not expected_workspace:
                return False
            if not same_resource_transition(old.get(names[key]), updated.get(names[key]),
                                            before.get(key, []), after.get(key, []), changes[key], expected_workspace):
                return False
            continue
        first, second = before.get(key, []), after.get(key, [])
        if not same_entries(first, second):
            return False
    if changes and not set(changes).issubset(set(before) | set(after)):
        return False
    return True


class AgentBuilderFlow:
    def __init__(self, execution, session_id, checks, allowed_names):
        self.execution, self.session_id, self.checks = execution, session_id, checks
        self.workspace = execution.root.rsplit('/', 1)[-1]
        self.allowed_names = set(allowed_names)
        self.current = None
        self.seen = {}
        self.revisions = {}
        self.absent_before = set()
        self.owned_published = set()
        self.created = None
        self.baseline_config = None

    def record(self, rule, passed, detail=''):
        self.checks.append({'session': self.session_id, 'rule': rule, 'passed': bool(passed),
                            'detail': '' if passed else detail})
        self.execution.report['validation'] = {'checks': self.checks}
        self.execution.checkpoint()

    def remember(self, task, name):
        output = candidate_output_from_task(task, name, self.workspace)
        self.remember_candidate(output, name)

    def remember_candidate(self, output, name):
        if not candidate_matches(output, name, self.workspace):
            output = None
        self.record('指定名称的候选已产生', output is not None, 'candidate_missing_or_mismatched')
        if not output:
            raise ProductCaseFailure('candidate_missing_or_mismatched')
        self.current = output
        self.seen[output['agent_id']] = ({key: output[key] for key in
                                        ('agent_id', 'candidate_version', 'source_digest', 'status')}, name)
        valid = output['status'] == 'valid'
        self.record('候选有效', valid, 'candidate_status:' + str(output['status']))
        if not valid:
            raise ProductCaseFailure('candidate_not_valid')
        absent = self.no_agent(output['agent_id'])
        self.record('确认前没有发布智能体', absent, 'agent_already_published')
        if not absent:
            raise ProductCaseFailure('agent_already_published')
        self.absent_before.add(output['agent_id'])

    def no_agent(self, agent_id):
        path = self.execution.root + '/agents/' + segment(agent_id) + '?' + urlencode({'agent_workspace_id': self.workspace})
        try:
            self.execution.client.product('GET', path)
            return False
        except Failure as error:
            if error.code == 'http_error' and error.http_status == 404:
                return True
            raise

    def require_resource(self, kind, resource_id, *, workspace_id=None):
        if kind not in {'skills', 'tools', 'knowledge_bases'}:
            raise Failure('invalid_builder_resource_kind')
        resolved = self.current.get('resolved_refs')
        refs = resolved.get(kind, []) if isinstance(resolved, dict) else []
        refs = refs if isinstance(refs, list) else []
        if workspace_id is not None and workspace_id not in {self.workspace, 'system'}:
            raise Failure('invalid_builder_resource_workspace')
        expected_workspace = workspace_id if workspace_id is not None else self.workspace if kind == 'knowledge_bases' else 'system'
        matches = [item for item in refs if isinstance(item, dict) and item.get('id') == resource_id and
                   resource_workspace(item, self.workspace) == expected_workspace and
                   isinstance(item.get('name'), str) and item['name']]
        label = {'skills': 'Skill', 'tools': '工具', 'knowledge_bases': '知识库'}[kind]
        self.record('候选绑定指定' + label + ' ' + resource_id, len(matches) == 1,
                    'resource_reference_missing_or_ambiguous:' + resource_id)
        if len(matches) != 1:
            raise ProductCaseFailure('resource_reference_missing_or_ambiguous')
        return {**matches[0], 'kind': kind, 'workspace_id': expected_workspace}

    def revise(self, task, name):
        old = self.current
        updated = candidate_output_from_task(task, name, self.workspace)
        self.record('追加对话产生修改后的候选', updated is not None, 'revised_candidate_missing')
        if not updated:
            raise ProductCaseFailure('revised_candidate_missing')
        self.current = updated
        self.seen[updated['agent_id']] = (candidate_from_task(task, name, self.workspace), name)
        valid = updated['status'] == 'valid'
        self.record('修改后的候选有效', valid, 'candidate_status:' + str(updated['status']))
        identity = (updated['agent_id'] == old['agent_id'] and
                    updated['candidate_version'] != old['candidate_version'] and
                    updated['source_digest'] != old['source_digest'])
        self.record('修改保留对象并生成新版本', identity, 'candidate_revision_identity_mismatch')
        self.record('未修改的模型与资源绑定保持不变', same_configuration(old, updated), 'unrequested_configuration_changed')
        if not valid or not identity:
            raise ProductCaseFailure('candidate_revision_invalid')
        path = (self.execution.root + '/agent-builder/candidates/' + segment(old['agent_id']) +
                '/candidate-versions/' + segment(old['candidate_version']) + '/commit')
        try:
            commit = self.execution.client.product('POST', path, {'source_digest': old['source_digest']})
            status = 200
            if (old['agent_id'] in self.absent_before and commit.get('agent_id') == old['agent_id'] and
                    commit.get('workspace_id') == self.workspace and commit.get('source_digest') == old['source_digest'] and
                    isinstance(commit.get('load_version'), str) and commit['load_version']):
                self.owned_published.add(old['agent_id'])
        except Failure as error:
            if error.code != 'http_error' or error.http_status is None:
                raise
            status = error.http_status
        self.record('旧候选不能再发布', status == 409, 'old_candidate_commit_status:' + str(status))
        absent = self.no_agent(old['agent_id'])
        self.record('拒绝旧候选后仍未发布智能体', absent, 'old_candidate_was_published')
        if status != 409 or not absent:
            raise ProductCaseFailure('old_candidate_commit_not_rejected')

    def revise_published(self, task, name, changes=None):
        old = self.current
        if not self.created or not self.created['verified']:
            raise ProductCaseFailure('created_agent_unavailable')
        updated = candidate_output_from_task(task, name, self.workspace, mode='revise')
        self.record('产生已发布智能体的修订候选', updated is not None, 'published_revision_missing')
        if not updated:
            raise ProductCaseFailure('published_revision_missing')
        valid = updated['status'] == 'valid'
        self.record('修订候选有效', valid, 'candidate_status:' + str(updated['status']))
        same_agent = (updated['agent_id'] == self.created['agent_id'] and
                      updated['candidate_version'] != old['candidate_version'] and
                      updated['source_digest'] != old['source_digest'])
        self.record('修订指向原智能体且生成新版本', same_agent, 'published_revision_identity_mismatch')
        self.record('修订符合请求且保留其他模型与资源', same_configuration(old, updated, changes, workspace_id=self.workspace),
                    'requested_or_unrequested_configuration_mismatch')
        if updated['agent_id'] == self.created['agent_id']:
            self.revisions[updated['agent_id']] = (candidate_from_task(task, name, self.workspace, mode='revise'), name)
        if not valid or not same_agent:
            raise ProductCaseFailure('published_revision_invalid')
        path = (self.execution.root + '/agents/' + segment(updated['agent_id']) + '/versions?' +
                urlencode({'agent_workspace_id': self.workspace}))
        versions = self.execution.client.product('GET', path).get('items', [])
        load_version = updated.get('load_version')
        unpublished = (isinstance(load_version, str) and bool(load_version) and
                       load_version != self.created['version'] and isinstance(versions, list) and
                       all(isinstance(item, dict) for item in versions) and
                       any(item.get('version') == self.created['version'] for item in versions) and
                       all(item.get('version') != load_version for item in versions))
        self.record('确认前没有发布修订版本', unpublished, 'revision_already_published')
        if not unpublished:
            raise ProductCaseFailure('revision_already_published')
        self.current = updated

    def commit(self, expected_resources=(), expected_description=None, *, capture_current=False, changes=None,
               expected_instruction=None, binding=None):
        output = self.current
        agent_id, candidate_version = output['agent_id'], output['candidate_version']
        if output['mode'] == 'create' and agent_id not in self.absent_before:
            raise ProductCaseFailure('agent_absence_not_verified')
        path = (self.execution.root + '/agent-builder/candidates/' + segment(agent_id) +
                '/candidate-versions/' + segment(candidate_version) + '/commit')
        try:
            body = {'source_digest': output['source_digest']}
            if binding is not None:
                body['binding'] = binding
            commit = self.execution.client.product('POST', path, body)
        except Failure as error:
            if error.code != 'http_error' or error.http_status is None:
                raise
            self.record('候选发布成功', False, 'commit_http_status:' + str(error.http_status))
            raise ProductCaseFailure('candidate_commit_failed') from None
        return self.verify_commit(commit, expected_resources, expected_description,
            capture_current=capture_current, changes=changes, expected_instruction=expected_instruction)

    def verify_commit(self, commit, expected_resources=(), expected_description=None, *,
                      capture_current=False, changes=None, expected_instruction=None):
        output = self.current
        agent_id = output['agent_id']
        version = commit.get('load_version')
        ok = (commit.get('agent_id') == agent_id and commit.get('workspace_id') == self.workspace and
              commit.get('source_digest') == output['source_digest'] and isinstance(version, str) and bool(version))
        self.record('候选发布成功', ok, 'commit_identity_mismatch')
        if not ok:
            raise ProductCaseFailure('candidate_commit_identity_mismatch')
        if output['mode'] == 'create':
            self.owned_published.add(agent_id)
        if agent_id in self.revisions:
            self.revisions[agent_id][0]['status'] = 'loaded'
        query = urlencode({'agent_workspace_id': self.workspace})
        try:
            agent = self.execution.client.product('GET', self.execution.root + '/agents/' + segment(agent_id) + '?' + query)
            versions = self.execution.client.product('GET', self.execution.root + '/agents/' + segment(agent_id) + '/versions?' + query)
        except Failure as error:
            if error.code != 'http_error' or error.http_status is None:
                raise
            self.record('发布的智能体可回读', False, 'readback_http_status:' + str(error.http_status))
            raise ProductCaseFailure('agent_readback_failed') from None
        name = output['display']['agent_name']
        identity = ((agent.get('id'), agent.get('workspace_id'), agent.get('name'), agent.get('status')) ==
                    (agent_id, self.workspace, name, 'active'))
        self.record('发布的智能体可回读', identity, 'agent_readback_mismatch')
        items = versions.get('items', [])
        items = items if isinstance(items, list) else []
        matching = [item for item in items if isinstance(item, dict) and item.get('version') == version and item.get('agent_id') == agent_id]
        manifest = matching[0].get('manifest', {}) if len(matching) == 1 else {}
        manifest = manifest if isinstance(manifest, dict) else {}
        config_ok = (len(matching) == 1 and matching[0].get('status') == 'runnable' and
                     manifest.get('id') == agent_id and manifest.get('name') == name and
                     manifest.get('model') == output.get('display', {}).get('model'))
        for expected_resource in expected_resources:
            refs = manifest.get('moiResourceRefs')
            manifest_kind = 'knowledgeBases' if expected_resource['kind'] == 'knowledge_bases' else expected_resource['kind']
            resources = refs.get(manifest_kind, []) if isinstance(refs, dict) else []
            resources = resources if isinstance(resources, list) else []
            config_ok = config_ok and any(item.get('name') == expected_resource['name'] and
                                          (expected_resource['kind'] == 'knowledge_bases' or
                                           resource_workspace(item, self.workspace) == expected_resource['workspace_id'])
                                          for item in resources if isinstance(item, dict))
        self.record('发布版本保留指定配置', config_ok, 'published_configuration_mismatch')
        self.execution.evidence.write('builder-published/' + segment(agent_id) + '/' + segment(version) + '.json', {'agent_id': agent_id, 'workspace_id': self.workspace,
            'version': version, 'name': name, 'manifest': manifest})
        self.created = {'agent_id': agent_id, 'workspace_id': self.workspace, 'version': version,
                        'verified': identity}
        knowledge = [resource for resource in expected_resources if resource['kind'] == 'knowledge_bases']
        if knowledge:
            try:
                bindings = self.execution.client.product('GET', self.execution.root + '/agents/' + segment(agent_id) +
                                                          '/bindings?' + query)
            except Failure as error:
                if error.code != 'http_error' or error.http_status is None:
                    raise
                self.record('发布智能体已绑定指定知识库', False, 'binding_readback_http_status:' + str(error.http_status))
                raise ProductCaseFailure('published_knowledge_binding_unavailable') from None
            actual = bindings.get('knowledge_bases')
            binding_ok = (bindings.get('agent_id') == agent_id and isinstance(actual, list) and
                          all(any(isinstance(item, dict) and item.get('id') == resource['id'] and
                                  item.get('workspace_id') == resource['workspace_id'] and
                                  item.get('name') == resource['name'] and item.get('status') == 'active'
                                  for item in actual) for resource in knowledge))
            self.record('发布智能体已绑定指定知识库', binding_ok, 'published_knowledge_binding_mismatch')
            self.execution.evidence.write('builder-published/' + segment(agent_id) + '/' + segment(version) + '-knowledge-bindings.json',
                                          {'agent_id': agent_id, 'knowledge_bases': actual})
            if not binding_ok:
                raise ProductCaseFailure('published_knowledge_binding_mismatch')
        if capture_current or expected_description is not None or changes is not None or expected_instruction is not None:
            try:
                current = self.execution.client.product('GET', self.execution.root + '/agent-builder/agents/' +
                    segment(agent_id) + '/versions/' + segment(version) + '/current-agent')
            except Failure as error:
                if error.code != 'http_error' or error.http_status is None:
                    raise
                current = {}
            self.execution.evidence.write('builder-published/' + segment(agent_id) + '/' + segment(version) + '-current.json', current)
            if capture_current:
                config_read = (current.get('agent_id') == agent_id and current.get('name') == name and
                               same_published_config(current, current))
                for expected_resource in expected_resources:
                    if config_read:
                        refs = current['resource_refs'].get(expected_resource['kind'], [])
                        config_read = isinstance(refs, list) and any(
                            isinstance(item, dict) and item.get('id') == expected_resource['id'] and
                            item.get('workspace_id') == expected_resource['workspace_id'] for item in refs)
                self.record('发布后的可编辑配置可回读', config_read, 'current_config_unavailable')
                if not config_read:
                    raise ProductCaseFailure('current_config_unavailable')
                self.baseline_config = current
            if expected_description is not None:
                description = current.get('description')
                description_ok = isinstance(description, str) and expected_description in description
                self.record('发布版本包含请求的描述', description_ok, 'description_readback_mismatch')
                if self.baseline_config is not None:
                    preserved = same_published_config(self.baseline_config, current)
                    self.record('发布修订保留原提示与资源', preserved, 'published_configuration_changed')
                else:
                    preserved = True
                if not description_ok or not preserved:
                    raise ProductCaseFailure('published_description_or_configuration_mismatch')
            if changes is not None:
                preserved = (self.baseline_config is not None and
                             same_published_config(self.baseline_config, current, changes, keep_description=True,
                                                   workspace_id=self.workspace))
                self.record('发布版本仅改变指定资源', preserved, 'published_resource_transition_mismatch')
                if not preserved:
                    raise ProductCaseFailure('published_resource_transition_mismatch')
            if expected_instruction is not None:
                baseline_instruction = (self.baseline_config or {}).get('agent_md')
                instruction = current.get('agent_md')
                instruction_ok = (isinstance(expected_instruction, str) and bool(expected_instruction) and
                                  isinstance(baseline_instruction, str) and expected_instruction not in baseline_instruction and
                                  isinstance(instruction, str) and expected_instruction in instruction and
                                  instruction != baseline_instruction)
                self.record('发布版本包含修改后的指令', instruction_ok, 'instruction_readback_mismatch')
                preserved = (self.baseline_config is not None and same_published_config(
                    self.baseline_config, current, keep_description=True, allow_instruction_change=True))
                self.record('指令修订保留其他配置', preserved, 'instruction_revision_changed_configuration')
                if not instruction_ok or not preserved:
                    raise ProductCaseFailure('published_instruction_or_configuration_mismatch')
        if not identity or not config_ok:
            raise ProductCaseFailure('published_agent_mismatch')
        return self.created

    def check_lifecycle(self):
        """Change only the agent published and identified by this case."""
        if (not self.created or not self.created['verified'] or
                self.created['agent_id'] not in self.owned_published):
            raise ProductCaseFailure('created_agent_unavailable')
        agent_id, name = self.created['agent_id'], self.current['display']['agent_name']
        query = urlencode({'agent_workspace_id': self.workspace})
        path = self.execution.root + '/agents/' + segment(agent_id) + '?' + query

        def product(method, target, body=None):
            try:
                return self.execution.client.product(method, target, body)
            except Failure as error:
                if error.code == 'product_error' or (error.code == 'http_error' and
                        type(error.http_status) is int and error.http_status not in {401, 403}):
                    self.record('智能体生命周期请求成功', False,
                                method + ':' + str(error.http_status or error.code))
                    raise ProductCaseFailure('agent_lifecycle_request_failed') from None
                raise

        def listing(status):
            query = urlencode({'catalog': 'workspace', 'query': name, 'status': status, 'limit': 100})
            result = product('GET', self.execution.root + '/agents?' + query)
            items = result.get('items')
            return isinstance(items, list) and any(isinstance(item, dict) and item.get('id') == agent_id and
                item.get('workspace_id') == self.workspace and item.get('status') == status for item in items)

        def identity(agent, status):
            return (agent.get('id'), agent.get('workspace_id'), agent.get('name'), agent.get('status')) == (
                agent_id, self.workspace, name, status)

        baseline = product('GET', path)
        if not identity(baseline, 'active'):
            raise ProductCaseFailure('agent_lifecycle_identity_mismatch')
        preserved_fields = ('description', 'instruction', 'metadata', 'runtime', 'binding', 'model', 'policy_refs')
        def preserved(agent):
            return all(agent.get(key) == baseline.get(key) for key in preserved_fields)

        self.record('新智能体出现在工作区列表', listing('active'), 'active_agent_missing_from_catalog')
        try:
            self.execution.client.product('POST', self.execution.root + '/agents',
                                          {'id': agent_id, 'name': name, 'status': 'active'})
            duplicate_status = 'accepted'
        except Failure as error:
            if error.code != 'http_error' or error.http_status is None:
                raise
            duplicate_status = error.http_status
        after_duplicate = product('GET', path)
        unchanged = identity(after_duplicate, 'active') and preserved(after_duplicate)
        self.record('重复智能体 ID 被拒绝且原对象未改变', duplicate_status == 409 and unchanged,
                    'duplicate_agent_not_rejected:' + str(duplicate_status))
        disabled = product('PATCH', path, {'status': 'disabled'})
        disabled_readback = product('GET', path)
        disabled_ok = (identity(disabled, 'disabled') and identity(disabled_readback, 'disabled') and
                       preserved(disabled_readback))
        self.record('禁用后详情回读为 disabled', disabled_ok, 'disabled_agent_readback_mismatch')
        if not disabled_ok:
            raise ProductCaseFailure('agent_disable_not_confirmed')
        self.record('禁用智能体可按状态筛选', listing('disabled'), 'disabled_agent_missing_from_catalog')
        active = product('PATCH', path, {'status': 'active'})
        active_readback = product('GET', path)
        active_ok = identity(active, 'active') and identity(active_readback, 'active') and preserved(active_readback)
        self.record('恢复后详情回读为 active', active_ok, 'restored_agent_readback_mismatch')
        if not active_ok:
            raise ProductCaseFailure('agent_restore_not_confirmed')
        self.record('恢复后的智能体出现在工作区列表', listing('active'), 'restored_agent_missing_from_catalog')
        self.execution.evidence.write('builder-published/' + segment(agent_id) + '/lifecycle.json',
            {'agent_id': agent_id, 'duplicate_status': duplicate_status, 'disabled': disabled_readback,
             'restored': active_readback})

    def cleanup(self):
        """Clean only identities observed in this case's own task outputs."""
        execution, client = self.execution, self.execution.client
        previous_deadline = client.deadline
        client.deadline = time.monotonic() + 60
        try:
            for entry in execution.tasks:
                if not entry['phase'].startswith(self.session_id + ':'):
                    continue
                try:
                    task = client.rpc(execution.conversation_selector(entry['conversation_id']),
                                      'tasks/get', {'id': entry['task_id']})
                    for output in candidate_outputs(task):
                        name = output.get('display', {}).get('agent_name')
                        if name in self.allowed_names:
                            mode = output.get('mode')
                            candidate = candidate_from_task(task, name, self.workspace, mode=mode)
                            if candidate and mode == 'create':
                                self.seen[candidate['agent_id']] = (candidate, name)
                            elif candidate and mode == 'revise' and candidate['agent_id'] in self.owned_published:
                                self.revisions[candidate['agent_id']] = (candidate, name)
                            else:
                                execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                                    'reason': 'candidate_identity_unverified'})
                        else:
                            execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                                'reason': 'candidate_identity_unverified'})
                except Exception as error:
                    failure = error_record('cleanup_agent', error)
                    failure['reason'] = 'candidate_readback:' + failure['reason']
                    execution.report['harness_errors'].append(failure)
            for agent_id, (candidate, _) in self.revisions.items():
                if candidate['status'] != 'valid':
                    continue
                path = (execution.root + '/agent-builder/candidates/' + segment(agent_id) +
                        '/candidate-versions/' + segment(candidate['candidate_version']) + '/cancel')
                try:
                    canceled = client.product('POST', path, {'source_digest': candidate['source_digest']})
                    if (canceled.get('agent_id') != agent_id or canceled.get('candidate_version') != candidate['candidate_version'] or
                            canceled.get('status') != 'superseded'):
                        raise Failure('revision_cancel_not_confirmed')
                except Exception as error:
                    failure = error_record('cleanup_agent', error)
                    failure['reason'] = 'revision_cancel:' + failure['reason']
                    execution.report['harness_errors'].append(failure)
            for agent_id, (candidate, name) in self.seen.items():
                path = execution.root + '/agents/' + segment(agent_id) + '?' + urlencode({'agent_workspace_id': self.workspace})
                try:
                    agent = client.product('GET', path)
                except Failure as error:
                    if error.code == 'http_error' and error.http_status == 404:
                        agent = None
                    else:
                        raise
                if agent:
                    if (agent.get('id') != agent_id or agent.get('workspace_id') != self.workspace or
                            agent.get('name') not in self.allowed_names):
                        raise Failure('cleanup_agent_identity_mismatch')
                    name = agent['name']
                execution.candidate, execution.commit = candidate, None
                execution.agent_name, execution.workspace_id = name, self.workspace
                cleanup_candidate(execution, allow_archive=agent_id in self.owned_published)
        except Exception as error:
            execution.report['harness_errors'].append(error_record('cleanup_agent', error))
        finally:
            client.deadline = previous_deadline
