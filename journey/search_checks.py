"""Verify that a final answer cites an observed real web-search result."""
import json
import re
from urllib.parse import urlsplit

SEARCH_TOOLS = {'moi_web_search', 'moi_bocha_web_search', 'moi_tavily_web_search', 'moi_brave_web_search'}
BLOCKED_CODES = {'WEB_QUOTA_EXCEEDED', 'WEB_AUTH_FAILED', 'WEB_PERMISSION_DENIED', 'WEB_UNAVAILABLE', 'WEB_RATE_LIMITED'}
URL_PATTERN = re.compile(r'''https?://[^\s<>\[\]"']+''', re.I)


def url_identity(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.rstrip('.,;:!?。！，；：)）'))
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        scheme, port = parsed.scheme.lower(), parsed.port
        if (scheme, port) in {('http', 80), ('https', 443)}:
            port = None
        return (scheme, parsed.hostname.lower(), port,
                parsed.path.rstrip('/') or '/', parsed.query)
    except (TypeError, ValueError):
        return None


def evaluate_search(path, task_id, reply, session_id, *, expected_tool=None):
    if expected_tool is not None and expected_tool not in SEARCH_TOOLS:
        raise ValueError('invalid_expected_search_tool')
    search_tools = {expected_tool} if expected_tool else SEARCH_TOOLS
    calls, results, opened, open_results = set(), [], {}, []
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict):
            continue
        tool = payload.get('tool_id') or payload.get('tool')
        if tool not in search_tools and tool != 'moi_web_open':
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if tool in search_tools:
            if event.get('kind') == 'tool_call':
                calls.add(call_id)
            elif event.get('kind') == 'tool_result':
                results.append((call_id, payload))
        elif event.get('kind') == 'tool_call' and isinstance(payload.get('arguments'), dict):
            opened[call_id] = url_identity(payload['arguments'].get('url'))
        elif event.get('kind') == 'tool_result':
            open_results.append((call_id, payload))
    urls, errors, truncated, unstructured = set(), set(), False, False
    for call_id, payload in results:
        if call_id not in calls:
            continue
        wrapper = payload.get('output')
        if not isinstance(wrapper, dict):
            unstructured |= payload.get('success') is True
            continue
        if wrapper.get('truncated') is True:
            truncated = True
        output = wrapper.get('output')
        if not isinstance(output, dict):
            unstructured |= payload.get('success') is True
            continue
        code = output.get('error_code')
        if isinstance(code, str) and code in BLOCKED_CODES:
            errors.add(code)
        if payload.get('success') is True and output.get('ok') is True and isinstance(output.get('results'), list):
            for item in output['results']:
                if isinstance(item, dict):
                    value = url_identity(item.get('url'))
                    if value:
                        urls.add(value)
        elif payload.get('success') is True and output.get('ok') is True:
            unstructured = True
    # An observed open operation may resolve a search result to a final URL.
    searched_urls = set(urls)
    for call_id, payload in open_results:
        if opened.get(call_id) not in searched_urls or payload.get('success') is not True:
            continue
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if isinstance(output, dict) and output.get('ok') is True:
            value = url_identity(output.get('url'))
            if value:
                urls.add(value)
    answer_urls = {value for match in URL_PATTERN.findall(reply) if (value := url_identity(match))}
    cited = bool(urls & answer_urls)
    blocked = None
    if not urls:
        if errors:
            blocked = sorted(errors)[0]
        elif truncated:
            blocked = 'search_result_truncated'
        elif unstructured:
            blocked = 'search_result_not_structured'
    checks = [
        {'session': session_id, 'rule': '调用联网搜索', 'passed': bool(calls),
         'detail': '' if calls else 'search_call_missing'},
        {'session': session_id, 'rule': '搜索返回可验证的来源', 'passed': bool(urls),
         'detail': '' if urls else blocked or 'search_result_unavailable'},
        {'session': session_id, 'rule': '答复引用实际搜索来源', 'passed': cited,
         'detail': '' if cited else blocked or 'search_source_not_cited'},
    ]
    return checks, blocked
