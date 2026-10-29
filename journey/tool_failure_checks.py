"""Verify deliberate tool failures using task-bound calls and their real results."""
import json
import re

from .client import Failure


def evaluate_tool_failure(path, task_id, session_id, config):
    kind = config.get('kind')
    if (kind not in {'arguments', 'timeout', 'artifact_size'} or
            kind != 'arguments' and (not isinstance(config.get('marker'), str) or not config['marker'])):
        raise Failure('invalid_tool_failure_case')
    tool_id = config.get('tool_id', 'mcp__moi-tools__bash')
    calls, results = {}, {}
    for line in path.read_text().splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict) or payload.get('tool_id') != tool_id:
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call':
            args = payload.get('arguments')
            if not isinstance(args, dict):
                continue
            command = args.get('command', '')
            matches = args == {'unexpected': 1} if kind == 'arguments' else (
                isinstance(command, str) and config['marker'] in command and
                (kind == 'timeout' and 'time.sleep(3)' in command and
                 type(args.get('timeout')) in {int, float} and 0 < args['timeout'] <= 1 or
                 kind == 'artifact_size' and 'moi-cli' in command and '--publish-artifact' in command))
            if matches:
                calls[call_id] = args
        elif event.get('kind') == 'tool_result' and call_id in calls:
            results[call_id] = payload
    rejected = []
    patterns = {
        'arguments': r'(?:unexpected|additional prop|schema|invalid argument|validation)',
        'timeout': r'(?:timed?\s*out|timeout|deadline exceeded)',
        'artifact_size': r'(?:upload.*limit|file.*too large|exceeds.*(?:size|limit)|413|32\s*(?:mi?b|mb))',
    }
    for call_id, payload in results.items():
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        # stdout may contain arbitrary input echo; require an actual failed tool or command.
        failed = payload.get('success') is False or (isinstance(output, dict) and
            type(output.get('exit_code')) is int and output['exit_code'] != 0)
        text = json.dumps(wrapper, ensure_ascii=False)
        if failed and re.search(patterns[kind], text, re.I) and (kind != 'arguments' or 'unexpected' in text.lower()):
            rejected.append(call_id)
    checks = [
        {'session': session_id, 'rule': '本任务实际调用指定错误边界 ' + kind, 'passed': bool(calls),
         'detail': '' if calls else 'expected_tool_call_missing:' + kind},
        {'session': session_id, 'rule': '对应工具明确报告预期错误 ' + kind, 'passed': bool(rejected),
         'detail': '' if rejected else 'expected_tool_failure_missing:' + kind},
    ]
    # Keep the original error in diagnostics; only annotate verified expected errors.
    expected = []
    for call_id in rejected:
        payload = results[call_id]
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if isinstance(output, dict) and type(output.get('exit_code')) is int and output['exit_code'] != 0:
            expected.append({'task_id': task_id, 'call_id': call_id, 'kind': 'command_exit', 'code': str(output['exit_code'])})
        if payload.get('success') is False and isinstance(payload.get('error'), str) and payload['error']:
            expected.append({'task_id': task_id, 'call_id': call_id, 'kind': 'tool_failure', 'code': payload['error']})
    if expected:
        checks[-1]['expected_tool_errors'] = expected
    return checks
