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
