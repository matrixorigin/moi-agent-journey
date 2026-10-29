"""Check the lifecycle and persisted transcript of a case-owned conversation."""

from urllib.parse import urlencode

from .builder_flow import ProductCaseFailure
from .client import Failure, segment


class ConversationChecks:
    def __init__(self, execution, conversation_id, session_id, checks):
        self.execution = execution
        self.conversation_id = conversation_id
        self.session_id = session_id
        self.checks = checks
        self.scope = execution.conversation_selector(conversation_id)
        self.base = execution.root + '/conversations'
        self.path = self.base + '/' + segment(conversation_id)

    def record(self, rule, passed, detail):
        self.checks.append({'session': self.session_id, 'rule': rule, 'passed': bool(passed),
                            'detail': '' if passed else detail})
        self.execution.report['validation'] = {'checks': self.checks}
        self.execution.checkpoint()

    def request(self, method, path, body, rule):
        try:
            return self.execution.client.product(method, path, body)
        except Failure as error:
            # Auth, transport and timeouts remain test-environment errors. A
            # product HTTP rejection of a valid operation is a functional failure.
            if error.code != 'product_error' and (error.code != 'http_error' or error.http_status in {None, 401, 403}):
                raise
            self.record(rule, False, 'product_error' if error.code == 'product_error' else
                        'http_status:' + str(error.http_status))
            raise ProductCaseFailure('conversation_operation_failed') from None

    def get(self):
        return self.request('GET', self.path + '?' + urlencode(self.scope), None, '会话详情可回读')

    def patch(self, body):
        return self.request('PATCH', self.path + '?' + urlencode(self.scope), body, '会话更新成功')

    def list_contains(self, **filters):
        query = {**self.scope, **filters, 'limit': 100, 'offset': 0}
        result = self.request('GET', self.base + '?' + urlencode(query), None, '会话列表可回读')
        items = result.get('items')
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            self.record('会话列表返回有效数据', False, 'invalid_conversation_list')
            raise ProductCaseFailure('invalid_conversation_list')
        matches = [item for item in items if item.get('id') == self.conversation_id]
        label = filters.get('status', 'all') + ('-pinned' if filters.get('pinned') else '')
        self.execution.evidence.write('sessions/' + self.session_id + '/conversation-list-' + label + '.json',
                                      {'filters': filters, 'total': result.get('total'), 'matches': matches})
        return bool(matches)

    def identity(self, item):
        return (item.get('id') == self.conversation_id and
                item.get('workspace_id') == self.execution.root.rsplit('/', 1)[-1] and
                item.get('agent_id') == self.scope['agent_id'] and
                item.get('agent_workspace_id') == self.scope['agent_workspace_id'])


def check_lifecycle(execution, conversation_id, session_id, checks, title):
    """Rename/pin, archive, then restore this case's conversation."""
    check = ConversationChecks(execution, conversation_id, session_id, checks)
    initial = check.get()
    execution.evidence.write('sessions/' + session_id + '/conversation-initial.json', initial)
    initial_ok = check.identity(initial) and initial.get('status') == 'active'
    check.record('新会话详情与智能体身份一致', initial_ok, 'conversation_identity_or_state_mismatch')
    if not initial_ok:
        raise ProductCaseFailure('conversation_identity_or_state_mismatch')

    check.patch({'title': title, 'pinned': True})
    renamed = check.get()
    execution.evidence.write('sessions/' + session_id + '/conversation-renamed.json', renamed)
    check.record('重命名和置顶持久化', check.identity(renamed) and renamed.get('title') == title and
                 renamed.get('pinned') is True and renamed.get('title_user_edited') is True,
                 'conversation_rename_or_pin_mismatch')
    check.record('置顶列表包含本会话', check.list_contains(keyword=title, pinned='true', status='active'),
                 'pinned_conversation_missing')

    check.patch({'status': 'archived'})
    archived = check.get()
    execution.evidence.write('sessions/' + session_id + '/conversation-archived.json', archived)
    check.record('归档状态和名称可回读', check.identity(archived) and archived.get('status') == 'archived' and
                 archived.get('title') == title and archived.get('pinned') is True,
                 'conversation_archive_mismatch')
    archived_list = check.list_contains(keyword=title, status='archived')
    active_list = check.list_contains(keyword=title, status='active')
    check.record('归档后列表状态正确', archived_list and not active_list, 'archived_list_mismatch')

    check.patch({'status': 'active', 'pinned': False})
    restored = check.get()
    execution.evidence.write('sessions/' + session_id + '/conversation-restored.json', restored)
    check.record('恢复会话并取消置顶', check.identity(restored) and restored.get('status') == 'active' and
                 restored.get('title') == title and restored.get('pinned') is False,
                 'conversation_restore_mismatch')
    active_list = check.list_contains(keyword=title, status='active')
    pinned_list = check.list_contains(keyword=title, status='active', pinned='true')
    check.record('恢复后列表状态正确', active_list and not pinned_list, 'restored_list_mismatch')


def check_history_and_invalid_update(execution, conversation_id, session_id, checks, title, prompts):
    """Read role-filtered messages and prove invalid mutations are atomic."""
    check = ConversationChecks(execution, conversation_id, session_id, checks)
    check.patch({'title': title, 'pinned': True})
    before = check.get()
    before_ok = check.identity(before) and before.get('title') == title and before.get('pinned') is True
    check.record('消息会话可定位', before_ok, 'conversation_identity_or_state_mismatch')
    if not before_ok:
        raise ProductCaseFailure('conversation_identity_or_state_mismatch')

    def messages(**filters):
        query = {**check.scope, **filters}
        return check.request('GET', check.path + '/messages?' + urlencode(query), None, '消息历史可回读')

    all_users = messages(role='user', limit=100, offset=0)
    users = all_users.get('items')
    expected_tasks = [item for item in execution.tasks if item.get('conversation_id') == conversation_id]
    task_ids = [item.get('task_id') for item in expected_tasks]
    user_ok = (isinstance(users, list) and len(task_ids) == len(prompts) == 2 and len(users) == 2 and
               all(isinstance(item, dict) and item.get('role') == 'user' and
                   item.get('conversation_id') == conversation_id for item in users) and
               all(isinstance(item.get('id'), str) and item['id'] for item in users) and
               len({item['id'] for item in users}) == 2 and
               [item.get('task_id') for item in users] == task_ids and
               all(isinstance(item.get('parts'), list) and any(
                   isinstance(part, dict) and part.get('kind') == 'text' and part.get('text') == prompt
                   for part in item['parts']) for item, prompt in zip(users, prompts)) and
               all_users.get('total') == 2)
    check.record('用户历史消息与实际对话一致', user_ok, 'conversation_user_history_mismatch')

    page_0 = messages(role='user', limit=1, offset=0)
    page_1 = messages(role='user', limit=1, offset=1)
    pagination_ok = (user_ok and page_0.get('total') == 2 and page_1.get('total') == 2 and
                     page_0.get('items') == users[:1] and page_1.get('items') == users[1:2])
    check.record('用户消息分页不丢失、不重复', pagination_ok, 'conversation_message_pagination_mismatch')

    agents = messages(role='agent', limit=100, offset=0)
    agent_items = agents.get('items')
    agent_ok = (isinstance(agent_items, list) and len(agent_items) == 2 and
                all(isinstance(item, dict) and item.get('role') == 'agent' and
                    item.get('conversation_id') == conversation_id for item in agent_items) and
                agents.get('total') == 2 and
                [item.get('task_id') for item in agent_items] == [item.get('task_id') for item in expected_tasks])
    check.record('智能体回复与两轮任务对应', agent_ok, 'conversation_agent_history_mismatch')
    execution.evidence.write('sessions/' + session_id + '/conversation-history.json',
                             {'users': all_users, 'user_page_0': page_0, 'user_page_1': page_1,
                              'agents': agents})

    path = check.path + '?' + urlencode(check.scope)
    invalid = {'title': 'invalid-' + title, 'pinned': False, 'status': 'invalid-status'}
    try:
        execution.client.product('PATCH', path, invalid)
        status = 'accepted'
    except Failure as error:
        if error.code != 'http_error' or error.http_status in {None, 401, 403}:
            raise
        status = error.http_status
    check.record('无效状态更新被拒绝', status == 400, 'invalid_update_status:' + str(status))
    after = check.get()
    execution.evidence.write('sessions/' + session_id + '/conversation-invalid-update.json',
                             {'before': before, 'invalid_status': status, 'after': after})
    check.record('无效更新没有改变会话', check.identity(after) and all(
        before.get(key) == after.get(key) for key in ('title', 'pinned', 'status')),
        'invalid_update_mutated_conversation')


def check_agent_scope(execution, conversation_id, other_conversation_id, session_id, checks):
    """Scope both list and detail reads to two distinct, case-owned Agents."""
    scopes = [ConversationChecks(execution, cid, session_id, checks) for cid in (conversation_id, other_conversation_id)]
    if conversation_id == other_conversation_id or scopes[0].scope == scopes[1].scope:
        raise Failure('invalid_conversation_scope_case')
    keyword = 'scope-' + execution.report['run_id']
    for index, check in enumerate(scopes):
        detail = check.get()
        valid = check.identity(detail)
        check.record('筛选前会话身份正确 ' + str(index), valid, 'conversation_identity_mismatch')
        if not valid:
            raise ProductCaseFailure('conversation_identity_mismatch')
        check.patch({'title': keyword + '-' + str(index)})
    evidence = []
    for index, check in enumerate(scopes):
        listed = check.request('GET', check.base + '?' + urlencode({**check.scope, 'keyword': keyword,
            'limit': 100, 'offset': 0}), None, '按智能体筛选会话')
        items = listed.get('items')
        check.record('智能体列表仅返回自己的测试会话 ' + str(index),
            listed.get('total') == 1 and isinstance(items, list) and len(items) == 1 and
            isinstance(items[0], dict) and check.identity(items[0]), 'conversation_agent_filter_mismatch')
        try:
            execution.client.product('GET', check.path + '?' + urlencode(scopes[1-index].scope))
            status = 'accepted'
        except Failure as error:
            if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                raise
            status = error.http_status
        check.record('错误智能体范围不能读取本会话 ' + str(index), status == 404,
                     'conversation_wrong_scope_status:' + str(status))
        evidence.append({'scope': check.scope, 'list': listed, 'wrong_scope_status': status})
    execution.evidence.write('sessions/' + session_id + '/conversation-scopes.json', evidence)


def check_archived_delivery(execution, conversation_id, task_id, session_id, checks, filename, content):
    import json
    from .a2a import final_reply
    import hashlib

    check = ConversationChecks(execution, conversation_id, session_id, checks)
    inventory = execution.evidence.root / 'artifacts' / conversation_id / 'inventory.json'
    def artifact_ref():
        matches = [item for item in json.loads(inventory.read_text())
                   if item.get('task_id') == task_id and item.get('file_name') == filename]
        if len(matches) != 1:
            raise ProductCaseFailure('artifact_identity_unverified')
        return {key: matches[0].get(key) for key in ('artifact_id', 'version', 'file_id', 'sha256')}
    before_ref = artifact_ref()
    messages_path = check.path + '/messages?' + urlencode({**check.scope, 'all': 'true', 'offset': 0})
    before_messages = check.request('GET', messages_path, None, '归档前历史可读')
    before_task = execution.client.rpc(check.scope, 'tasks/get', {'id': task_id})
    check.patch({'status': 'archived'})
    archived = check.get()
    check.record('会话归档后身份和状态可回读', check.identity(archived) and archived.get('status') == 'archived',
                 'archived_conversation_mismatch')
    after_messages = check.request('GET', messages_path, None, '归档后历史可读')
    items = before_messages.get('items')
    has_history = (isinstance(items, list) and {item.get('role') for item in items if isinstance(item, dict) and
                   item.get('task_id') == task_id and item.get('conversation_id') == conversation_id} >= {'user', 'agent'})
    check.record('归档后保留原用户消息和智能体回复', has_history and before_messages == after_messages,
                 'archived_history_missing_or_changed')
    after_task = execution.client.rpc(check.scope, 'tasks/get', {'id': task_id})
    try:
        task_preserved = (after_task.get('id') == task_id and after_task.get('contextId') == conversation_id and
                          bool(final_reply(before_task)) and final_reply(after_task) == final_reply(before_task))
    except (Failure, KeyError, TypeError):
        task_preserved = False
    check.record('归档后原任务及最终答复仍可读取', task_preserved, 'archived_task_changed')
    artifact_path = (execution.root + '/agent-artifacts/' + segment(before_ref['artifact_id']) +
                     '/versions/' + str(before_ref['version']))
    query = urlencode({**check.scope, 'conversation_id': conversation_id, 'page': 1, 'page_size': 100})
    archived_list = check.request('GET', execution.root + '/agent-artifacts?' + query, None, '归档后成果列表可读')
    check.record('归档会话的成果从活动列表退出', archived_list.get('total') == 0 and archived_list.get('items') == [],
                 'archived_artifact_still_listed')
    try:
        execution.client.product('GET', artifact_path)
        status = 'present'
    except Failure as error:
        if error.code != 'http_error' or error.http_status in {None, 401, 403}:
            raise
        status = error.http_status
    check.record('归档会话的成果详情暂不可读', status == 404, 'archived_artifact_detail_status:' + str(status))
    check.patch({'status': 'active'})
    restored = check.get()
    check.record('恢复会话状态', check.identity(restored) and restored.get('status') == 'active', 'conversation_restore_mismatch')
    detail = check.request('GET', artifact_path, None, '恢复后原成果可读')
    restored_ok = (all(detail.get(key) == before_ref[key] for key in ('artifact_id', 'version', 'file_id')) and
                   detail.get('conversation_id') == conversation_id and detail.get('task_id') == task_id)
    check.record('恢复后原成果身份与任务关联不变', restored_ok, 'restored_artifact_identity_mismatch')
    # Fetch again from QA, never validate this transition against the cached file.
    body = execution.client.request('GET', execution.root + '/files/' + segment(before_ref['file_id']) + '/download', binary=True)
    check.record('恢复后重新下载的成果字节一致', hashlib.sha256(body).hexdigest() == before_ref['sha256'] and
        body in (content.encode(), content.encode() + b'\n', content.encode() + b'\r\n'), 'restored_artifact_content_mismatch')
    execution.evidence.write('sessions/' + session_id + '/archived-delivery.json', {
        'before_messages': before_messages, 'after_messages': after_messages, 'before_artifact': before_ref,
        'after_task': after_task, 'archived': archived, 'archived_artifact_list': archived_list,
        'archived_artifact_status': status, 'restored': restored, 'restored_artifact': detail})
