"""Check that a requested Python calculation ran in the Agent sandbox."""
import hashlib
import json
import re


def evaluate_python_hash(path, task_id, session_id, content, reply):
    if not isinstance(content, str) or not content:
        raise ValueError('invalid_sandbox_assertion')
    expected = hashlib.sha256(content.encode('utf-8')).hexdigest()
    calls, matches, results = set(), set(), {}
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if (event.get('task_id') != task_id or not isinstance(payload, dict) or
                payload.get('tool_id') != 'mcp__moi-tools__bash'):
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call':
            arguments = payload.get('arguments')
            command = arguments.get('command', '') if isinstance(arguments, dict) else ''
            if isinstance(command, str) and re.search(r'\bpython(?:3(?:\.\d+)?)?\b', command):
                calls.add(call_id)
                if (re.match(r'^\s*(?:/[^\s]*/)?python(?:3(?:\.\d+)?)?\b', command) and content in command and
                        re.search(r'\bhashlib\b', command) and re.search(r'\bsha256\b', command, re.I)):
                    matches.add(call_id)
        elif event.get('kind') == 'tool_result':
            results[call_id] = payload
    output_ok, unknown = False, bool(calls and not matches)
    pattern = re.compile(r'(?<![0-9a-f])' + expected + r'(?![0-9a-f])', re.I)
    for call_id in matches:
        payload = results.get(call_id)
        wrapper = payload.get('output') if isinstance(payload, dict) else None
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if not isinstance(output, dict) or type(output.get('exit_code')) is not int:
            unknown = True
            continue
        if payload.get('success') is False or output['exit_code'] != 0:
            continue
        if payload.get('success') is not True or not isinstance(output.get('stdout'), str):
            unknown = True
            continue
        if pattern.search(output['stdout']):
            output_ok = True
        elif output.get('output_truncated') is True:
            unknown = True
    blocked = 'sandbox_output_unverifiable' if not output_ok and unknown else None
    return [
        {'session': session_id, 'rule': '沙箱中实际运行指定 Python 计算', 'passed': bool(matches),
         'detail': '' if matches else blocked or 'python_call_missing'},
        {'session': session_id, 'rule': 'Python 输出与本地计算的哈希一致', 'passed': output_ok,
         'detail': '' if output_ok else blocked or 'python_output_mismatch'},
        {'session': session_id, 'rule': '最终答复包含正确哈希', 'passed': bool(pattern.search(reply)),
         'detail': '' if pattern.search(reply) else 'answer_hash_mismatch'},
    ], blocked
