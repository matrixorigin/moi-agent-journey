"""Exercise invalid message admission without retrying the request."""

import uuid
from urllib.parse import urlencode

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
    execution.evidence.append('dialogue.jsonl', {'at': utc_now(), 'phase': session_id + ':empty_message',
        'conversation_id': conversation_id, 'message_id': message_id, 'control': 'empty_message'})
    execution.pending_conversation = conversation_id
    execution.checkpoint()
    try:
        result = execution.client.rpc(selector, 'message/send', params)
        status = 'accepted'
    except Failure as error:
        if error.code == 'http_error' and error.http_status not in {None, 401, 403}:
            status = error.http_status
        elif error.code == 'rpc_error' and isinstance(error.details, dict):
            status = 'rpc:' + str(error.details.get('code'))
        else:
            raise
    record('空消息被明确拒绝', status in {400, 'rpc:-32602'}, 'empty_message_status:' + str(status))
    after = conversation_task_ids(execution, conversation_id)
    unchanged = before == after
    record('空消息没有创建任务', unchanged, 'empty_message_created_task')
    execution.evidence.write('sessions/' + session_id + '/empty-message.json',
                             {'message_id': message_id, 'status': status,
                              'before_task_ids': sorted(before), 'after_task_ids': sorted(after)})
    if unchanged:
        execution.pending_conversation = None
        execution.checkpoint()
    else:
        # Existing cleanup recovers and stops the newly admitted task, if its
        # outcome can be identified; the invalid request is never replayed.
        raise ProductCaseFailure('empty_message_created_task')
