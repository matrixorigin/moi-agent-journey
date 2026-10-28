"""Check real Agent Builder candidates, publication and identity-bound cleanup."""
import json
import time
from urllib.parse import urlencode

from .agent_builder import candidate_from_task, candidate_output_from_task, candidate_outputs, cleanup_candidate
from .client import Failure, segment


class ProductCaseFailure(Exception):
    """A product prerequisite failed; later dependent actions cannot be sent."""


def same_entries(before, after):
    return (isinstance(before, list) and isinstance(after, list) and
            sorted(json.dumps(item, sort_keys=True) for item in before) ==
            sorted(json.dumps(item, sort_keys=True) for item in after))


def same_resource_transition(before_names, after_names, before_refs, after_refs, changes):
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
            matches = [item for item in source if item.get('id') == resource_id and item.get('workspace_id') == 'system']
            if (len(matches) != 1 or any(item.get('id') == resource_id and item.get('workspace_id') == 'system'
                                         for item in destination) or
                    not isinstance(matches[0].get('name'), str) or matches[0]['name'] not in source_names):
                return False
            source.remove(matches[0])
            source_names.remove(matches[0]['name'])
    return same_entries(before_refs, after_refs) and same_entries(before_names, after_names)


def same_configuration(old, updated, changes=None):
    old_display, new_display = old.get('display', {}), updated.get('display', {})
    old_refs, new_refs = old.get('resolved_refs'), updated.get('resolved_refs')
    if (old_display.get('model') != new_display.get('model') or
            not isinstance(old_refs, dict) or not isinstance(new_refs, dict)):
        return False
    for field in ('skills', 'tools', 'knowledge_bases', 'channel_bindings'):
        if changes and field in changes:
            if not same_resource_transition(old_display.get(field), new_display.get(field),
                                            old_refs.get(field, []), new_refs.get(field, []), changes[field]):
                return False
            continue
        pairs = ((old_display.get(field), new_display.get(field)),
                 (old_refs.get(field, []), new_refs.get(field, [])))
        if any(not same_entries(before, after) for before, after in pairs):
            return False
    return True


def same_published_config(old, updated, changes=None, *, keep_description=False):
    for key in ('agent_id', 'name', 'model_name'):
        if not isinstance(old.get(key), str) or not old[key] or old[key] != updated.get(key):
            return False
    if (not isinstance(old.get('agent_md'), str) or old['agent_md'] != updated.get('agent_md') or
            old.get('model_config_ref') != updated.get('model_config_ref')):
        return False
    if keep_description and (not isinstance(old.get('description'), str) or old['description'] != updated.get('description')):
        return False
    names = {'skills': 'skill_names', 'tools': 'tool_names'}
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
            if not same_resource_transition(old.get(names[key]), updated.get(names[key]),
                                            before.get(key, []), after.get(key, []), changes[key]):
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
        self.created = None
        self.baseline_config = None

    def record(self, rule, passed, detail=''):
        self.checks.append({'session': self.session_id, 'rule': rule, 'passed': bool(passed),
                            'detail': '' if passed else detail})
        self.execution.report['validation'] = {'checks': self.checks}
        self.execution.checkpoint()

    def remember(self, task, name):
        output = candidate_output_from_task(task, name, self.workspace)
        self.record('指定名称的候选已产生', output is not None, 'candidate_missing_or_mismatched')
        if not output:
            raise ProductCaseFailure('candidate_missing_or_mismatched')
        self.current = output
        self.seen[output['agent_id']] = (candidate_from_task(task, name, self.workspace), name)
        valid = output['status'] == 'valid'
        self.record('候选有效', valid, 'candidate_status:' + str(output['status']))
        if not valid:
            raise ProductCaseFailure('candidate_not_valid')
        absent = self.no_agent(output['agent_id'])
        self.record('确认前没有发布智能体', absent, 'agent_already_published')
        if not absent:
            raise ProductCaseFailure('agent_already_published')

    def no_agent(self, agent_id):
        path = self.execution.root + '/agents/' + segment(agent_id) + '?' + urlencode({'agent_workspace_id': self.workspace})
        try:
            self.execution.client.product('GET', path)
            return False
        except Failure as error:
            if error.code == 'http_error' and error.http_status == 404:
                return True
            raise

    def require_resource(self, kind, resource_id):
        if kind not in {'skills', 'tools'}:
            raise Failure('invalid_builder_resource_kind')
        resolved = self.current.get('resolved_refs')
        refs = resolved.get(kind, []) if isinstance(resolved, dict) else []
        refs = refs if isinstance(refs, list) else []
        matches = [item for item in refs if isinstance(item, dict) and item.get('id') == resource_id and
                   item.get('workspace_id') == 'system' and isinstance(item.get('name'), str) and item['name']]
        label = 'Skill' if kind == 'skills' else '工具'
        self.record('候选绑定指定' + label, len(matches) == 1, 'resource_reference_missing_or_ambiguous')
        if len(matches) != 1:
            raise ProductCaseFailure('resource_reference_missing_or_ambiguous')
        return {**matches[0], 'kind': kind}

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
            self.execution.client.product('POST', path, {'source_digest': old['source_digest']})
            status = 200
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
        self.record('修订符合请求且保留其他模型与资源', same_configuration(old, updated, changes),
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

    def commit(self, expected_resource=None, expected_description=None, *, capture_current=False, changes=None):
        output = self.current
        agent_id, candidate_version = output['agent_id'], output['candidate_version']
        path = (self.execution.root + '/agent-builder/candidates/' + segment(agent_id) +
                '/candidate-versions/' + segment(candidate_version) + '/commit')
        try:
            commit = self.execution.client.product('POST', path, {'source_digest': output['source_digest']})
        except Failure as error:
            if error.code != 'http_error' or error.http_status is None:
                raise
            self.record('候选发布成功', False, 'commit_http_status:' + str(error.http_status))
            raise ProductCaseFailure('candidate_commit_failed') from None
        version = commit.get('load_version')
        ok = (commit.get('agent_id') == agent_id and commit.get('workspace_id') == self.workspace and
              commit.get('source_digest') == output['source_digest'] and isinstance(version, str) and bool(version))
        self.record('候选发布成功', ok, 'commit_identity_mismatch')
        if not ok:
            raise ProductCaseFailure('candidate_commit_identity_mismatch')
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
        if expected_resource:
            refs = manifest.get('moiResourceRefs')
            resources = refs.get(expected_resource['kind'], []) if isinstance(refs, dict) else []
            resources = resources if isinstance(resources, list) else []
            config_ok = config_ok and any(item.get('name') == expected_resource['name'] and
                                          item.get('workspace_id') == expected_resource['workspace_id']
                                          for item in resources if isinstance(item, dict))
        self.record('发布版本保留指定配置', config_ok, 'published_configuration_mismatch')
        self.execution.evidence.write('builder-published/' + segment(version) + '.json', {'agent_id': agent_id, 'workspace_id': self.workspace,
            'version': version, 'name': name, 'manifest': manifest})
        self.created = {'agent_id': agent_id, 'workspace_id': self.workspace, 'version': version,
                        'verified': identity}
        if capture_current or expected_description is not None or changes is not None:
            try:
                current = self.execution.client.product('GET', self.execution.root + '/agent-builder/agents/' +
                    segment(agent_id) + '/versions/' + segment(version) + '/current-agent')
            except Failure as error:
                if error.code != 'http_error' or error.http_status is None:
                    raise
                current = {}
            self.execution.evidence.write('builder-published/' + segment(version) + '-current.json', current)
            if capture_current:
                config_read = (current.get('agent_id') == agent_id and current.get('name') == name and
                               same_published_config(current, current))
                if expected_resource and config_read:
                    refs = current['resource_refs'].get(expected_resource['kind'], [])
                    config_read = config_read and isinstance(refs, list) and any(
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
                             same_published_config(self.baseline_config, current, changes, keep_description=True))
                self.record('发布版本仅改变指定资源', preserved, 'published_resource_transition_mismatch')
                if not preserved:
                    raise ProductCaseFailure('published_resource_transition_mismatch')
        if not identity or not config_ok:
            raise ProductCaseFailure('published_agent_mismatch')
        return self.created

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
                            elif candidate and mode == 'revise' and candidate['agent_id'] in self.seen:
                                self.revisions[candidate['agent_id']] = (candidate, name)
                            else:
                                execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                                    'reason': 'candidate_identity_unverified'})
                        else:
                            execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                                'reason': 'candidate_identity_unverified'})
                except Exception as error:
                    execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                        'reason': 'candidate_readback:' + type(error).__name__})
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
                    execution.report['harness_errors'].append({'stage': 'cleanup_agent',
                        'reason': 'revision_cancel:' + type(error).__name__})
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
                cleanup_candidate(execution)
        except Exception as error:
            execution.report['harness_errors'].append({'stage': 'cleanup_agent', 'reason': type(error).__name__})
        finally:
            client.deadline = previous_deadline
