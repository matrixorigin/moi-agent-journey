"""Verify a specific web-open call and its returned content."""
import json

from .search_checks import BLOCKED_CODES, url_identity


def evaluate_open(path, task_id, session_id, expected_url, expected_content):
    target = url_identity(expected_url)
    if not target or not isinstance(expected_content, str) or not expected_content:
        raise ValueError('invalid_web_open_assertion')
    calls, results = set(), {}
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict) or payload.get('tool_id') != 'moi_web_open':
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call':
            args = payload.get('arguments')
            if isinstance(args, dict) and url_identity(args.get('url')) == target:
                calls.add(call_id)
        elif event.get('kind') == 'tool_result':
            results[call_id] = payload
    opened, blocked = False, None
    for call_id in calls:
        payload = results.get(call_id)
        if not payload:
            blocked = blocked or 'open_result_unavailable'
            continue
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if not isinstance(output, dict):
            blocked = blocked or 'open_result_unavailable'
            continue
        code = output.get('error_code')
        if isinstance(code, str) and code in BLOCKED_CODES:
            blocked = blocked or code
        if (payload.get('success') is True and output.get('ok') is True and
                url_identity(output.get('url')) == target and isinstance(output.get('content'), str)):
            if expected_content in output['content']:
                opened = True
            elif wrapper.get('truncated') is True:
                blocked = blocked or 'open_content_truncated'
    if opened:
        blocked = None
    checks = [
        {'session': session_id, 'rule': '实际打开指定网页', 'passed': bool(calls),
         'detail': '' if calls else 'open_call_missing_or_url_mismatch'},
        {'session': session_id, 'rule': '网页返回预期内容', 'passed': opened,
         'detail': '' if opened else blocked or 'open_content_mismatch_or_failed'},
    ]
    return checks, blocked
