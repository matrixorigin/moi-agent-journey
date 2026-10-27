"""Summarize explicit tool failures while retaining the full event stream."""
import json
from pathlib import Path


def observed_tool_errors(path):
    observations = []
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('invalid_event_record')
            kind = event.get('kind')
            if kind not in {'tool_result', 'lifecycle'}:
                continue
            payload = event.get('payload')
            if not isinstance(payload, dict):
                raise ValueError('invalid_event_payload')
            if kind == 'lifecycle' and payload.get('type') != 'tool_transport_failed':
                continue
            context = {'task_id': event.get('task_id'), 'seq': event.get('seq'),
                       'tool': payload.get('tool'), 'call_id': payload.get('call_id')}
            if not context['task_id'] or not isinstance(context['seq'], int):
                raise ValueError('tool_event_missing_identity')
            if kind == 'lifecycle':
                observations.append({**context, 'kind': 'tool_transport_failed',
                                     'error_kind': payload.get('error_kind'),
                                     'detail': str(payload.get('error') or '')[:2048]})
                continue
            output = payload.get('output')
            if payload.get('success') is False or payload.get('status') == 'failed':
                detail = output if isinstance(output, str) else payload.get('error', '')
                observations.append({**context, 'kind': 'tool_failure', 'error_kind': payload.get('error_kind'),
                                     'code': payload.get('error'), 'detail': str(detail)[:2048]})
            if not isinstance(output, dict) or not isinstance(output.get('output'), dict):
                continue
            terminal = output['output']
            for channel in ('stdout', 'stderr'):
                text = terminal.get(channel)
                if not isinstance(text, str):
                    continue
                for raw in text.splitlines():
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
                        observations.append({**context, 'kind': 'tool_output_http_error', 'channel': channel,
                                             'status': status, 'code': code,
                                             'request_id': message.get('request_id')})
    return observations
