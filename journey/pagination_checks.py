"""Compare filtered pages to the exact conversations and tasks created by this case."""
from urllib.parse import urlencode

from .client import Failure
from .conversation_checks import ConversationChecks


def check_pagination(execution, conversation_id, session_id, checks):
    check = ConversationChecks(execution, conversation_id, session_id, checks)
    owned = execution.conversations
    if len(owned) != 3 or any(item.get('selector') != check.scope for item in owned):
        raise Failure('invalid_pagination_fixture')
    expected = {item['id'] for item in owned}
    keyword = 'journey-' + execution.case['id'] + '-' + execution.report['run_id'] + '-'
    # Automatic titles may replace initial titles after inference; pin explicit test names now.
    for index, item in enumerate(owned):
        owner = ConversationChecks(execution, item['id'], session_id, checks)
        if not owner.identity(owner.get()):
            raise Failure('pagination_conversation_identity_unverified')
        owner.patch({'title': keyword + str(index)})
    def pages(path, filters, total):
        responses = [check.request('GET', path + '?' + urlencode({**check.scope, **filters, 'limit': 1, 'offset': i}),
                                   None, '分页接口可读取') for i in range(total + 1)]
        execution.evidence.append('pagination.jsonl', {'path': path, 'filters': filters, 'pages': responses})
        valid = all(type(page.get('total')) is int and page['total'] == total and isinstance(page.get('items'), list) and
                    len(page['items']) == (1 if i < total else 0) for i, page in enumerate(responses))
        return [row for page in responses for row in page.get('items', [])] if valid else None
    items = pages(check.base, {'keyword': keyword, 'status': 'active'}, 3)
    check.record('会话分页完整且没有重复或混入其他会话', items is not None and
        {item.get('id') for item in items} == expected and len(items) == len(expected), 'conversation_pages_mismatch')
    check.patch({'pinned': True})
    pinned = pages(check.base, {'keyword': keyword, 'status': 'active', 'pinned': 'true'}, 1)
    check.record('会话分页置顶过滤正确', pinned is not None and pinned[0].get('id') == conversation_id,
                 'conversation_page_filter_mismatch')
    expected_tasks = {item['task_id'] for item in execution.tasks if item['conversation_id'] == conversation_id}
    tasks = pages(execution.root + '/agent-runtime-tasks', {'context_id': conversation_id}, len(expected_tasks))
    check.record('任务分页完整且限定当前会话', tasks is not None and len(tasks) == len(expected_tasks) and
        {item.get('id') for item in tasks} == expected_tasks and all(item.get('context_id') == conversation_id for item in tasks),
        'task_pages_mismatch')
    for role in ('user', 'agent'):
        messages = pages(check.path + '/messages', {'role': role}, len(expected_tasks))
        check.record(role + ' 历史分页不丢失、不重复并对应实际任务', messages is not None and
            len({item.get('id') for item in messages}) == len(expected_tasks) and
            {item.get('task_id') for item in messages} == expected_tasks and
            all(item.get('role') == role and item.get('conversation_id') == conversation_id for item in messages),
            'message_pages_mismatch:' + role)
