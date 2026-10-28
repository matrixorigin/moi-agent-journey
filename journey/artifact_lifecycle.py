"""Delete and verify only an artifact produced by the current test task."""

import json
from urllib.parse import urlencode

from .builder_flow import ProductCaseFailure
from .client import Failure, segment


def check_artifact_delete(execution, conversation_id, task_id, filename, checks, session_id):
    inventory = execution.evidence.root / 'artifacts' / conversation_id / 'inventory.json'
    items = json.loads(inventory.read_text())
    matching = [item for item in items if item.get('task_id') == task_id and item.get('file_name') == filename]
    selector = execution.conversation_selector(conversation_id)
    workspace_id = execution.root.rsplit('/', 1)[-1]
    artifact = matching[0] if len(matching) == 1 else {}
    artifact_id, version = artifact.get('artifact_id'), artifact.get('version')
    owned = (len(matching) == 1 and artifact.get('conversation_id') == conversation_id and
             artifact.get('workspace_id') == workspace_id and artifact.get('agent_id') == selector['agent_id'] and
             artifact.get('agent_workspace_id') == selector['agent_workspace_id'] and
             isinstance(artifact_id, str) and bool(artifact_id) and type(version) is int and version > 0)

    def record(rule, passed, detail):
        checks.append({'session': session_id, 'rule': rule, 'passed': bool(passed),
                       'detail': '' if passed else detail})
        execution.report['validation'] = {'checks': checks}
        execution.checkpoint()

    def request(method, path, body, rule):
        try:
            return execution.client.product(method, path, body)
        except Failure as error:
            if error.code != 'product_error' and (error.code != 'http_error' or error.http_status in {None, 401, 403}):
                raise
            record(rule, False, 'product_error' if error.code == 'product_error' else
                   'http_status:' + str(error.http_status))
            raise ProductCaseFailure('artifact_operation_failed') from None

    record('只选择当前任务产生的成果', owned, 'artifact_identity_unverified')
    if not owned:
        raise ProductCaseFailure('artifact_identity_unverified')
    ref = {'artifact_id': artifact_id, 'version': version}
    execution.evidence.write('sessions/' + session_id + '/artifact-before-delete.json', artifact)
    deleted = request('POST', execution.root + '/agent-artifacts/batch-delete', {'items': [ref]}, '删除请求返回本成果成功')
    execution.evidence.write('sessions/' + session_id + '/artifact-delete.json', deleted)
    record('删除请求返回本成果成功', deleted.get('succeeded') == [ref] and deleted.get('failed') == [],
           'artifact_delete_result_mismatch')

    detail_path = (execution.root + '/agent-artifacts/' + segment(artifact_id) + '/versions/' + str(version))
    try:
        detail = execution.client.product('GET', detail_path)
        detail_status = 'present'
    except Failure as error:
        if error.code != 'http_error' or error.http_status in {None, 401, 403}:
            raise
        detail, detail_status = None, error.http_status
    record('删除后成果详情不存在', detail_status == 404, 'artifact_detail_status:' + str(detail_status))

    resolved = request('POST', execution.root + '/agent-artifacts/resolve', {'items': [ref]}, '删除后成果不能再解析')
    failures = resolved.get('failed')
    resolve_ok = (resolved.get('items') == [] and isinstance(failures, list) and len(failures) == 1 and
                  isinstance(failures[0], dict) and
                  failures[0].get('artifact_id') == artifact_id and failures[0].get('version') == version and
                  failures[0].get('reason') == 'not_found')
    record('删除后成果不能再解析', resolve_ok, 'artifact_resolve_mismatch')
    listed = request('GET', execution.root + '/agent-artifacts?' + urlencode({
        **selector, 'conversation_id': conversation_id, 'keyword': filename, 'page': 1, 'page_size': 100}),
        None, '删除后成果不在会话列表')
    listed_items = listed.get('items')
    list_ok = (isinstance(listed_items, list) and type(listed.get('total')) is int and
               listed['total'] == len(listed_items) and all(isinstance(item, dict) for item in listed_items) and
               all(not (item.get('artifact_id') == artifact_id and item.get('version') == version)
                   for item in listed_items))
    record('删除后成果不在会话列表', list_ok, 'deleted_artifact_still_listed')
    execution.evidence.write('sessions/' + session_id + '/artifact-after-delete.json',
                             {'detail_status': detail_status, 'detail': detail, 'resolve': resolved,
                              'conversation_list': listed})
