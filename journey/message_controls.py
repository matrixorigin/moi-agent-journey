"""Exercise invalid message admission without retrying the request."""

import uuid
from urllib.parse import urlencode

from .a2a import final_reply
from .builder_flow import ProductCaseFailure
from .client import Failure
from .evidence import utc_now


def conversation_task_ids(execution, conversation_id):
    selector = execution.conversation_selector(conversation_id)
    path = execution.root + '/agent-runtime-tasks?' + urlencode({
        **selector, 'context_id': conversation_id, 'limit': 100, 'offset': 0})
    page = execution.client.product('GET', path)
    items = page.get('items')
    if (not isinstance(items, list) or type(page.get('total')) is not int or
            page['total'] != len(items) or any(not isinstance(item, dict) or
            item.get('context_id') != conversation_id or not isinstance(item.get('id'), str) or not item['id']
            for item in items)):
        raise Failure('invalid_conversation_task_list')
    ids = {item['id'] for item in items}
    if len(ids) != len(items):
        raise Failure('invalid_conversation_task_list')
    return ids


def check_empty_message(execution, conversation_id, session_id, checks):
    check_invalid_message(execution, conversation_id, session_id, checks, 'empty_message')


def check_invalid_message(execution, conversation_id, session_id, checks, defect, *, knowledge_id=None):
    labels = {'empty_message': '空消息', 'role': '无效消息角色', 'message_id': '缺失 messageId',
              'attachment_uri': '缺失附件 URI', 'files_shape': '非数组附件元数据',
              'knowledge_negative': '负数知识库 ID', 'knowledge_shape': '非数组知识库选择',
              'deleted_knowledge': '已删除的知识库选择'}
    if defect not in labels or (defect == 'deleted_knowledge' and
            (not isinstance(knowledge_id, str) or not knowledge_id.isdigit() or int(knowledge_id) <= 0)):
        raise Failure('invalid_message_defect')
    label = labels[defect]
    selector = execution.conversation_selector(conversation_id)

    def record(rule, passed, detail):
        checks.append({'session': session_id, 'rule': rule, 'passed': bool(passed),
                       'detail': '' if passed else detail})
        execution.report['validation'] = {'checks': checks}
        execution.checkpoint()

    before = conversation_task_ids(execution, conversation_id)
    known = {item.get('task_id') for item in execution.tasks if item.get('conversation_id') == conversation_id}
    known_ok = bool(known) and before == known
    record('已有任务在当前会话中可回读', known_ok, 'conversation_task_list_mismatch')
    if not known_ok:
        raise ProductCaseFailure('conversation_task_list_mismatch')
    message_id = str(uuid.uuid4())
    params = {'message': {'kind': 'message', 'role': 'user', 'messageId': message_id,
              'contextId': conversation_id, 'parts': []}, 'model': execution.model,
              'idempotencyKey': message_id}
    message = params['message']
    if defect != 'empty_message':
        message['parts'] = [{'kind': 'text', 'text': 'Synthetic admission check.'}]
    if defect == 'role':
        message['role'] = 'agent'
    elif defect == 'message_id':
        message.pop('messageId')
    elif defect == 'attachment_uri':
        message['parts'] = [{'kind': 'file', 'file': {'name': 'missing-uri.txt', 'mimeType': 'text/plain'}}]
    elif defect == 'files_shape':
        message['metadata'] = {'files': {'invalid': True}}
    elif defect in {'knowledge_negative', 'knowledge_shape', 'deleted_knowledge'}:
        value = [-1] if defect == 'knowledge_negative' else {'invalid': True} if defect == 'knowledge_shape' else [knowledge_id]
        message['metadata'] = {'semantic_model_ids': value}
    execution.evidence.append('dialogue.jsonl', {'at': utc_now(), 'phase': session_id + ':' + defect,
        'conversation_id': conversation_id, 'message_id': message_id, 'control': defect})
    execution.pending_conversation = conversation_id
    execution.checkpoint()
    previous_observer = execution.client.observer
    def observe(exchange):
        if previous_observer:
            previous_observer(exchange)
        execution.evidence.append('message-controls-http.jsonl', {
            'control': defect, 'conversation_id': conversation_id, **exchange})
    execution.client.observer = observe
    try:
        response = execution.client.rpc(selector, 'message/send', params)
        status = 'accepted'
    except Failure as error:
        response = {'error': error.code, 'http_status': error.http_status, 'details': error.details}
        if error.code == 'http_error' and error.http_status not in {None, 401, 403}:
            status = error.http_status
        elif error.code == 'rpc_error' and isinstance(error.details, dict):
            status = 'rpc:' + str(error.details.get('code'))
        else:
            raise
    finally:
        execution.client.observer = previous_observer
    expected = {400, 'rpc:-32602'}
    if defect == 'deleted_knowledge':
        expected |= {404, 'rpc:-32001'}
    record(label + '被明确拒绝', status in expected, defect + '_status:' + str(status))
    after = conversation_task_ids(execution, conversation_id)
    unchanged = before == after
    record(label + '没有创建任务', unchanged, defect + '_created_task')
    execution.evidence.write('sessions/' + session_id + '/' + defect.replace('_', '-') + '.json',
                             {'message_id': message_id, 'status': status, 'request': params, 'response': response,
                              'before_task_ids': sorted(before), 'after_task_ids': sorted(after)})
    if unchanged:
        execution.pending_conversation = None
        execution.checkpoint()
    else:
        # Existing cleanup recovers and stops the newly admitted task, if its
        # outcome can be identified; the invalid request is never replayed.
        raise ProductCaseFailure(defect + '_created_task')


def check_completed_cancellation(execution, conversation_id, session_id, task, checks):
    """A late cancellation must preserve a completed task and its answer."""
    selector = execution.conversation_selector(conversation_id)
    task_id = task['id']
    answer = final_reply(task)

    def record(rule, passed, detail):
        checks.append({'session': session_id, 'rule': rule, 'passed': bool(passed),
                       'detail': '' if passed else detail})
        execution.report['validation'] = {'checks': checks}
        execution.checkpoint()

    def is_completed(value):
        return (isinstance(value, dict) and value.get('kind') == 'task' and value.get('id') == task_id and
                value.get('contextId') == conversation_id and isinstance(value.get('status'), dict) and
                value['status'].get('state') == 'completed')

    try:
        cancellation = execution.client.rpc(selector, 'tasks/cancel', {'id': task_id})
        cancel_result = cancellation
    except Failure as error:
        if ((error.code == 'http_error' and error.http_status not in {None, 401, 403}) or
                error.code == 'rpc_error'):
            cancellation = None
            cancel_result = {'error': error.code, 'http_status': error.http_status, 'details': error.details}
        else:
            raise
    record('取消已完成任务返回原完成状态', is_completed(cancellation), 'late_cancel_response_mismatch')
    after = execution.client.rpc(selector, 'tasks/get', {'id': task_id})
    record('已完成任务状态保持不变', is_completed(after), 'late_cancel_changed_task')
    try:
        answer_preserved = is_completed(after) and final_reply(after) == answer
    except (Failure, KeyError, TypeError):
        answer_preserved = False
    record('取消后仍可读取原答复', answer_preserved, 'late_cancel_changed_answer')
    execution.evidence.write('sessions/' + session_id + '/late-cancellation.json',
                             {'before': task, 'cancel': cancel_result, 'after': after})
