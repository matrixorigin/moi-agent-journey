"""Exercise an intentional stream disconnect while an Agent task is running."""
import contextlib
import json

from .client import Failure
from .evidence import utc_now
from .hello import task_state


def live_resume_stream(execution, selector, params, conversation_id, phase):
    """Send once, disconnect after admission, and resubscribe to the same task."""
    with contextlib.closing(execution.client.stream_rpc(selector, params)) as original:
        try:
            first = next(original)
        except StopIteration:
            raise Failure('stream_ended_without_task') from None
        yield first
        entry = execution.tasks[-1]
        try:
            cursor = int(first['sequence'])
            if cursor <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise Failure('live_resume_invalid_cursor') from None
        task = execution.client.rpc(selector, 'tasks/get', {'id': entry['task_id']})
        state = task_state(task, conversation_id, entry['task_id'])
        entry['state'] = state
        entry['live_resume'] = {'cursor': cursor, 'state_before_disconnect': state, 'resumed': False}
        execution.checkpoint()
        if state != 'working':
            return
    entry['live_resume']['resumed'] = True
    execution.checkpoint()
    last, final_seen = cursor, False
    try:
        with contextlib.closing(execution.client.stream_rpc(
                selector, {'id': entry['task_id'], 'afterSeq': cursor}, method='tasks/resubscribe')) as stream:
            for event in stream:
                execution.evidence.append('live-resume.jsonl', {'recorded_at': utc_now(), 'phase': phase,
                    'conversation_id': conversation_id, **event})
                result = event['result']
                try:
                    sequence = int(event['sequence'])
                except (KeyError, TypeError, ValueError):
                    raise Failure('live_resume_invalid_sequence') from None
                if sequence <= last or final_seen:
                    raise Failure('live_resume_invalid_sequence')
                if (result.get('taskId', result.get('id')) != entry['task_id'] or
                        result.get('contextId') != conversation_id):
                    raise Failure('live_resume_identity_mismatch')
                last, final_seen = sequence, result.get('final') is True
                yield event
    except Failure as error:
        if error.code.startswith('live_resume_') or error.code in {'case_timeout', 'deadline_exceeded', 'transport_error'}:
            raise
        raise Failure('live_resume_rejected', error.http_status,
                      {'code': error.code, 'details': error.details}) from None
    entry['live_resume'].update({'final_seq': last, 'final_seen': final_seen})
    execution.checkpoint()


def check_live_resume(execution, entry, checks, session_id):
    """Compare the resumed stream with the task's persisted public events."""
    info = entry.get('live_resume')
    if (not isinstance(info, dict) or type(info.get('cursor')) is not int or info['cursor'] <= 0 or
            not isinstance(info.get('state_before_disconnect'), str) or type(info.get('resumed')) is not bool):
        raise Failure('invalid_live_resume_evidence')
    working = info['state_before_disconnect'] == 'working'
    if info['resumed'] != working or (working and (type(info.get('final_seq')) is not int or
                                                    type(info.get('final_seen')) is not bool)):
        raise Failure('invalid_live_resume_evidence')
    blocked = None if working else 'task_not_running_before_disconnect'
    replay_path = execution.evidence.root / 'live-resume.jsonl'
    replay = [json.loads(line) for line in replay_path.read_text().splitlines()] if replay_path.exists() else []
    replay = [item for item in replay if item.get('phase') == entry['phase']]
    events_path = execution.evidence.root / 'events.jsonl'
    if not events_path.is_file():
        raise Failure('missing_recorded_events')
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    try:
        cursor, final_seq = info['cursor'], info['final_seq']
        resumed = [(int(item['sequence']), item['result']['metadata']['eventKind']) for item in replay]
        task_events = [item for item in events if item.get('task_id') == entry['task_id']]
        persisted = [(item['seq'], item['kind']) for item in task_events if cursor < item['seq'] <= final_seq]
        cursor_present = any(item['seq'] == cursor for item in task_events)
        terminal = [item for item in task_events if item['seq'] == final_seq]
        final = (bool(replay and replay[-1]['result'].get('final') is True and info['final_seen'] is True) and
                 replay[-1]['result'].get('status', {}).get('state') == 'completed' and
                 len(terminal) == 1 and terminal[0].get('a2a_state') == 'completed')
        ordered = (bool(resumed) and all(cursor < value[0] <= final_seq for value in resumed) and
                   [value[0] for value in resumed] == sorted({value[0] for value in resumed}))
        matched = cursor_present and ordered and resumed == persisted
    except (KeyError, TypeError, ValueError):
        final, ordered, matched = False, False, False
    checks.extend([
        {'session': session_id, 'rule': '断线前任务处于运行中', 'passed': working,
         'detail': '' if working else blocked},
        {'session': session_id, 'rule': '原任务续读事件并到达结束状态', 'passed': working and ordered and final,
         'detail': '' if working and ordered and final else blocked or 'live_resume_incomplete'},
        {'session': session_id, 'rule': '续读事件与持久化事件一致', 'passed': working and matched,
         'detail': '' if working and matched else blocked or 'live_resume_events_mismatch'},
    ])
    return blocked
