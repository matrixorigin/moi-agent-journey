"""Check an observed Skill call and its result in the same Agent task."""
import json


def check_skill_call(path, task_id, skill_id, session_id):
    calls, results = set(), {}
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict) or payload.get('tool_id') != 'skill':
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        arguments = payload.get('arguments')
        if event.get('kind') == 'tool_call' and isinstance(arguments, dict) and arguments.get('skill_name') == skill_id:
            calls.add(call_id)
        elif event.get('kind') == 'tool_result':
            results[call_id] = payload.get('success')
    succeeded = any(results.get(call_id) is True for call_id in calls)
    unknown = bool(calls) and not succeeded and any(call_id not in results or results[call_id] is None for call_id in calls)
    return ({'session': session_id, 'rule': '实际调用指定 Skill 并成功 (' + skill_id + ')', 'passed': succeeded,
             'detail': '' if succeeded else 'skill_result_unavailable' if unknown else 'skill_call_not_successful'},
            'skill_result_unavailable' if unknown else None)
