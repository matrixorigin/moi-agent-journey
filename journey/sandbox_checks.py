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


def evaluate_python_recovery(path, task_id, session_id, marker, left, right, *, require_failed_tool=False):
    """Require an observed Python failure followed by a separate successful call."""
    if (not isinstance(marker, str) or not marker or type(left) is not int or type(right) is not int or
            type(require_failed_tool) is not bool):
        raise ValueError('invalid_sandbox_recovery_assertion')
    calls, results = {}, {}
    for position, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        event = json.loads(line)
        payload = event.get('payload')
        if (event.get('task_id') != task_id or not isinstance(payload, dict) or
                payload.get('tool_id') != 'mcp__moi-tools__bash'):
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call':
            args = payload.get('arguments')
            command = args.get('command') if isinstance(args, dict) else None
            if isinstance(command, str) and re.search(r'\bpython(?:3(?:\.\d+)?)?\b', command):
                calls[call_id] = (position, command)
        elif event.get('kind') == 'tool_result':
            wrapper = payload.get('output')
            output = wrapper.get('output') if isinstance(wrapper, dict) else None
            results[call_id] = (position, payload, output)

    failure_positions, success_positions = [], []
    unknown = False
    expected = str(left * right)
    for call_id, (call_position, command) in calls.items():
        is_failure = marker in command
        is_recovery = str(left) in command and str(right) in command
        if not is_failure and not is_recovery:
            continue
        if not re.match(r'^\s*(?:/[^\s]*/)?python(?:3(?:\.\d+)?)?\b', command):
            unknown = True
            continue
        result = results.get(call_id)
        if not result or not isinstance(result[2], dict) or type(result[2].get('exit_code')) is not int:
            unknown = True
            continue
        result_position, payload, output = result
        if result_position <= call_position:
            unknown = True
            continue
        stderr, stdout = output.get('stderr'), output.get('stdout')
        if is_failure:
            if (isinstance(stderr, str) and marker in stderr and 'ValueError' in stderr and
                    (not require_failed_tool or output['exit_code'] != 0)):
                failure_positions.append(result_position)
            elif output.get('output_truncated') is True or not isinstance(stderr, str):
                unknown = True
        if is_recovery and output['exit_code'] == 0 and payload.get('success') is True:
            if isinstance(stdout, str) and re.search(r'(?<!\d)' + re.escape(expected) + r'(?!\d)', stdout):
                success_positions.append(call_position)
            elif output.get('output_truncated') is True or not isinstance(stdout, str):
                unknown = True
    recovered = any(success > failure for failure in failure_positions for success in success_positions)
    blocked = 'sandbox_recovery_evidence_unverifiable' if unknown and not recovered else None
    return [
        {'session': session_id, 'rule': ('观察到指定的 Python 错误且工具返回非零状态' if require_failed_tool
                                        else '观察到指定的 Python 错误'), 'passed': bool(failure_positions),
         'detail': '' if failure_positions else blocked or 'expected_python_failure_missing'},
        {'session': session_id, 'rule': '错误后独立运行 Python 并得到正确结果', 'passed': recovered,
         'detail': '' if recovered else blocked or 'python_recovery_missing'},
    ], blocked
