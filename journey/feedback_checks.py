"""Submit feedback on this case's answer and verify its persisted identity."""

import uuid
from urllib.parse import urlencode

from .builder_flow import ProductCaseFailure
from .client import Failure
from .message_controls import conversation_task_ids


def check_feedback(execution, conversation_id, session_id, task, checks, *, rating='up', comment=None, labels=None,
                   check_filter=False):
    if (rating not in {'up', 'down', 'neutral'} or (comment is not None and
            (not isinstance(comment, str) or not comment)) or (labels is not None and
            (not isinstance(labels, list) or any(not isinstance(label, str) or not label for label in labels))) or
            type(check_filter) is not bool):
        raise Failure('invalid_feedback_case')
    selector = execution.conversation_selector(conversation_id)
    task_id = task.get('id')
    message = task.get('status', {}).get('message', {})
    assistant_id = message.get('messageId') if isinstance(message, dict) else None

    def record(rule, passed, detail):
        checks.append({'session': session_id, 'rule': rule, 'passed': bool(passed),
                       'detail': '' if passed else detail})
        execution.report['validation'] = {'checks': checks}
        execution.checkpoint()

    target_ok = (task.get('contextId') == conversation_id and isinstance(task_id, str) and task_id and
                 isinstance(message, dict) and message.get('role') == 'agent' and
                 message.get('taskId') == task_id and message.get('contextId') == conversation_id and
                 isinstance(assistant_id, str) and bool(assistant_id))
    record('反馈目标是本任务的智能体回复', target_ok, 'feedback_target_mismatch')
    if not target_ok:
        raise ProductCaseFailure('feedback_target_mismatch')
    known = {item['task_id'] for item in execution.tasks if item.get('conversation_id') == conversation_id}
    if conversation_task_ids(execution, conversation_id) != known:
        raise Failure('conversation_task_list_mismatch')

    def feedback_list(message_id, rating_filter=None):
        query = {**selector, 'message_id': message_id, 'limit': 100, 'offset': 0}
        if rating_filter is not None:
            query['rating'] = rating_filter
        try:
            return execution.client.product('GET', execution.root + '/feedback?' + urlencode(query))
        except Failure as error:
            if error.code != 'product_error' and (error.code != 'http_error' or error.http_status in {None, 401, 403}):
                raise
            record('反馈列表可回读', False, 'feedback_read_failed:' + str(error.http_status or error.code))
            raise ProductCaseFailure('feedback_read_failed') from None

    def control(feedback_id, message_id, target_id):
        data = {
                                'type': 'moi.feedback.submit', 'version': 1, 'feedbackId': feedback_id,
                                'target': {'agentId': selector['agent_id'], 'messageId': target_id},
                                'rating': rating, 'intent': 'record_only'}
        if comment is not None:
            data['comment'] = comment
        if labels is not None:
            data['labels'] = labels
        return {'message': {'kind': 'message', 'role': 'user', 'messageId': message_id,
                            'contextId': conversation_id, 'parts': [{'kind': 'data', 'data': data}]}}

    invalid_id, valid_id = 'j2-' + uuid.uuid4().hex, 'j2-' + uuid.uuid4().hex
    missing_message_id = 'missing-' + uuid.uuid4().hex
    invalid_params = control(invalid_id, str(uuid.uuid4()), missing_message_id)
    execution.pending_conversation = conversation_id
    execution.checkpoint()
    try:
        execution.client.rpc(selector, 'message/send', invalid_params)
        status = 'accepted'
    except Failure as error:
        if error.code == 'http_error' and error.http_status not in {None, 401, 403}:
            status = error.http_status
        elif error.code == 'rpc_error' and isinstance(error.details, dict):
            status = 'rpc:' + str(error.details.get('code'))
        else:
            raise
    record('不存在的反馈目标被拒绝', status in {400, 'rpc:-32602'}, 'invalid_feedback_status:' + str(status))
    invalid_list = feedback_list(missing_message_id)
    record('无效反馈没有被保存', invalid_list.get('total') == 0 and invalid_list.get('items') == [],
           'invalid_feedback_persisted')
    if conversation_task_ids(execution, conversation_id) != known:
        record('反馈操作没有创建智能体任务', False, 'feedback_created_task')
        raise ProductCaseFailure('feedback_created_task')
    execution.pending_conversation = None
    execution.checkpoint()

    params = control(valid_id, str(uuid.uuid4()), assistant_id)
    execution.pending_conversation = conversation_id
    execution.checkpoint()
    try:
        receipt = execution.client.rpc(selector, 'message/send', params)
    except Failure as error:
        if ((error.code == 'http_error' and type(error.http_status) is int and 400 <= error.http_status < 500
             and error.http_status not in {401, 403}) or error.code == 'rpc_error'):
            record('有效反馈被接受', False, 'feedback_rejected:' + str(error.http_status or error.code))
            if conversation_task_ids(execution, conversation_id) == known:
                execution.pending_conversation = None
                execution.checkpoint()
            raise ProductCaseFailure('feedback_rejected') from None
        raise
    parts = receipt.get('parts')
    data = parts[0].get('data') if (isinstance(parts, list) and len(parts) == 1 and
            isinstance(parts[0], dict) and parts[0].get('kind') == 'data') else None
    target = data.get('target') if isinstance(data, dict) else None
    receipt_ok = (receipt.get('kind') == 'message' and receipt.get('role') == 'agent' and
                  receipt.get('contextId') == conversation_id and receipt.get('messageId') == params['message']['messageId'] and
                  isinstance(data, dict) and data.get('type') == 'moi.feedback.received' and data.get('version') == 1 and
                  data.get('feedbackId') == valid_id and data.get('status') == 'recorded' and
                  isinstance(target, dict) and target.get('agentId') == selector['agent_id'] and
                  target.get('contextId') == conversation_id and target.get('taskId') == task_id and
                  target.get('messageId') == assistant_id)
    record('反馈回执指向原始答复', receipt_ok, 'feedback_receipt_mismatch')
    saved = feedback_list(assistant_id)
    items = saved.get('items')
    feedback = items[0] if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) else {}
    saved_ok = (saved.get('total') == 1 and feedback.get('id') == valid_id and
                feedback.get('workspace_id') == execution.root.rsplit('/', 1)[-1] and
                feedback.get('agent_id') == selector['agent_id'] and feedback.get('context_id') == conversation_id and
                feedback.get('task_id') == task_id and feedback.get('message_id') == assistant_id and
                feedback.get('rating') == rating and feedback.get('intent') == 'record_only' and
                (comment is None or feedback.get('comment') == comment) and
                (labels is None or feedback.get('labels') == labels))
    record('反馈可按答复回读且内容正确', saved_ok, 'feedback_persistence_mismatch')
    execution.evidence.write('sessions/' + session_id + '/feedback.json',
                             {'invalid_status': status, 'invalid_list': invalid_list,
                              'receipt': receipt, 'saved': saved})
    if check_filter:
        matching = feedback_list(assistant_id, rating)
        opposite = feedback_list(assistant_id, 'up' if rating != 'up' else 'down')
        record('评价类型筛选能找到对应反馈', matching == saved, 'feedback_rating_filter_mismatch')
        record('其他评价类型不会匹配', opposite.get('total') == 0 and opposite.get('items') == [],
               'feedback_opposite_filter_mismatch')
        execution.evidence.write('sessions/' + session_id + '/feedback-filter.json',
                                 {'matching': matching, 'opposite': opposite})

    # Replaying this specific request deliberately tests feedback idempotency.
    replay = execution.client.rpc(selector, 'message/send', params)
    replay_list = feedback_list(assistant_id)
    record('同一反馈重放不产生重复记录', replay == receipt and replay_list == saved,
           'feedback_replay_changed_result')
    execution.evidence.write('sessions/' + session_id + '/feedback-replay.json',
                             {'receipt': replay, 'saved': replay_list})
    unchanged = conversation_task_ids(execution, conversation_id) == known
    record('反馈操作没有创建智能体任务', unchanged, 'feedback_created_task')
    if not unchanged:
        raise ProductCaseFailure('feedback_created_task')
    execution.pending_conversation = None
    execution.checkpoint()
