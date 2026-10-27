"""Derive tool-call observations from the public, already-redacted event stream."""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


CLI_ERROR = re.compile(r'^\s*Error:\s*\[([A-Za-z][\w.-]*)\]\s*(.*)$')


def success_rate(succeeded, failed):
    known = succeeded + failed
    return succeeded / known if known else None


def _output_observations(output):
    """Return explicit errors and whether an embedded command itself failed."""
    if not isinstance(output, dict) or not isinstance(output.get('output'), dict):
        return [], False
    terminal = output['output']
    observations = []
    exit_code = terminal.get('exit_code')
    failed = isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0
    if failed:
        stderr, stdout = terminal.get('stderr'), terminal.get('stdout')
        detail = str(stderr) if stderr else '未返回 stderr；stdout: ' + str(stdout or '无')
        observations.append({'kind': 'command_exit', 'code': str(exit_code), 'detail': str(detail)[:4096]})
    if terminal.get('ok') is False:
        failed = True
        observations.append({'kind': 'tool_output_failure', 'code': terminal.get('error_code'),
                             'detail': str(terminal.get('message') or '')[:4096],
                             'provider': terminal.get('provider')})
    for channel in ('stdout', 'stderr'):
        text = terminal.get(channel)
        if not isinstance(text, str):
            continue
        for raw in text.splitlines():
            match = CLI_ERROR.match(raw)
            if match:
                observations.append({'kind': 'reported_cli_error', 'channel': channel,
                                     'code': match[1], 'detail': match[2][:4096]})
                continue
            if not raw.strip().startswith('{'):
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            status, code = message.get('status'), message.get('code')
            if isinstance(status, int) and not isinstance(status, bool) and status >= 400 and isinstance(code, str):
                observations.append({'kind': 'reported_http_error', 'channel': channel, 'status': status,
                                     'code': code, 'request_id': message.get('request_id'),
                                     'detail': str(message.get('message') or '')[:4096]})
    return observations, failed


def analyze_events(path, tasks):
    """Group events by task/call ID; exclude evaluator calls from agent metrics.

    A success is a reported successful execution, not proof that a tool's answer
    was semantically correct. Skipped calls and ambiguous outputs are reported
    separately and excluded from the success-rate denominator.
    """
    by_id = {task['task_id']: task for task in tasks if task.get('task_id')}
    agent_ids = {task_id for task_id, task in by_id.items()
                 if not str(task.get('phase', '')).startswith('judge:')}
    calls, rounds, active_round, sessions, event_counts = {}, set(), {}, {}, Counter()
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('invalid_event_record')
            task_id, kind = event.get('task_id'), event.get('kind')
            if task_id not in by_id:
                raise ValueError('event_task_not_in_case')
            event_counts[task_id] += 1
            if kind not in {'lifecycle', 'tool_call', 'tool_result'}:
                continue
            payload = event.get('payload')
            if not isinstance(payload, dict):
                raise ValueError('invalid_event_payload')
            event_type = payload.get('type')
            if kind == 'lifecycle' and event_type == 'session_info':
                values = [payload.get('session_id'), payload.get('external_session_id')]
                ids = [value for value in values if isinstance(value, str) and value
                       and value.lower() not in {'[redacted]', '[withheld]'}]
                session = sessions.setdefault(task_id, {'ids': [], 'redacted': False})
                for value in ids:
                    if value not in session['ids']:
                        session['ids'].append(value)
                session['redacted'] |= any(isinstance(value, str) and value.lower() == '[redacted]'
                                           for value in values)
            if kind == 'lifecycle' and event_type == 'llm_started':
                active_round.pop(task_id, None)
            elif kind == 'lifecycle' and event_type == 'llm_finished':
                identity = payload.get('call_id') or payload.get('turn')
                if identity is not None:
                    active_round[task_id] = str(identity)
            if kind not in {'tool_call', 'tool_result'} and not (
                    kind == 'lifecycle' and event_type == 'tool_transport_failed'):
                continue
            seq = event.get('seq')
            if not isinstance(seq, int) or isinstance(seq, bool):
                raise ValueError('tool_event_missing_identity')
            call_id = payload.get('call_id')
            identified = isinstance(call_id, str) and bool(call_id)
            if not identified:
                # A result without an ID cannot safely be joined to another event.
                call_id = 'unidentified-event-' + str(seq)
            key = (task_id, call_id)
            call = calls.setdefault(key, {'task_id': task_id, 'call_id': call_id,
                'phase': by_id[task_id].get('phase'), 'tool': None, 'arguments': None,
                'seqs': [], 'round': None, 'started': False, 'result_seen': False,
                'reported_success': False, 'failed': False, 'skipped': False, 'errors': [],
                'identified': identified})
            call['seqs'].append(seq)
            tool = payload.get('tool_id') or payload.get('tool')
            if isinstance(tool, str) and tool and tool != '(surgically_removed)':
                call['tool'] = tool
            if payload.get('arguments') is not None and (kind == 'tool_call' or call['arguments'] is None):
                call['arguments'] = payload['arguments']
            if kind == 'tool_call':
                call['started'] = True
                call['round'] = active_round.get(task_id)
                if task_id in agent_ids and call['identified'] and call['round'] is not None:
                    rounds.add((task_id, call['round']))
            elif kind == 'lifecycle':
                call['failed'] = True
                call['errors'].append({'kind': 'tool_transport_failed', 'seq': seq,
                    'code': payload.get('error_kind'), 'detail': str(payload.get('error') or '')[:4096]})
            else:
                call['result_seen'] = True
                skipped = payload.get('status') == 'skipped'
                call['skipped'] |= skipped
                if not skipped and (payload.get('success') is False or payload.get('status') in {'failed', 'rejected'}):
                    call['failed'] = True
                    detail = payload.get('output') if isinstance(payload.get('output'), str) else payload.get('error', '')
                    call['errors'].append({'kind': 'tool_failure', 'seq': seq, 'code': payload.get('error'),
                                           'detail': str(detail)[:4096], 'error_kind': payload.get('error_kind')})
                if payload.get('success') is True:
                    call['reported_success'] = True
                observations, failed = _output_observations(payload.get('output'))
                call['failed'] |= failed
                for item in observations:
                    call['errors'].append({'seq': seq, **item})
    for task_id, task in by_id.items():
        expected = task.get('event_count')
        if isinstance(expected, int) and not isinstance(expected, bool) and event_counts[task_id] != expected:
            raise ValueError('event_count_mismatch:' + task_id)
    counts = {'calls': 0, 'succeeded': 0, 'failed': 0, 'skipped': 0, 'unknown': 0,
              'unidentified_events': 0}
    errors, judge_errors, by_tool = [], [], defaultdict(Counter)
    unmapped = 0
    for call in calls.values():
        if call['failed']:
            state = 'failed'
        elif call['skipped']:
            state = 'skipped'
        elif call['reported_success'] and not call['errors']:
            state = 'succeeded'
        else:
            state = 'unknown'
        if call['task_id'] not in agent_ids:
            if call['errors']:
                judge_errors.append({key: call[key] for key in ('task_id', 'call_id', 'phase', 'tool', 'arguments', 'seqs', 'errors')}
                                    | {'outcome': state})
            continue
        if call['identified']:
            counts['calls'] += 1
            counts[state] += 1
            by_tool[call['tool'] or 'unknown']['calls'] += 1
            by_tool[call['tool'] or 'unknown'][state] += 1
            if call['round'] is None:
                unmapped += 1
        else:
            counts['unidentified_events'] += len(call['seqs'])
        if call['errors']:
            errors.append({key: call[key] for key in ('task_id', 'call_id', 'phase', 'tool', 'arguments', 'seqs', 'errors')}
                          | {'outcome': state})
    per_tool = []
    for tool, data in sorted(by_tool.items()):
        item = {'tool': tool, **{key: data[key] for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown')}}
        item['success_rate'] = success_rate(item['succeeded'], item['failed'])
        per_tool.append(item)
    counts.update({'success_rate': success_rate(counts['succeeded'], counts['failed']),
                   'tool_rounds': len(rounds), 'round_unmapped_calls': unmapped, 'by_tool': per_tool})
    return {'metrics': counts, 'errors': errors, 'judge_errors': judge_errors, 'runtime_sessions': sessions}
