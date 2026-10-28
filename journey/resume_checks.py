"""Verify that a completed Agent task can replay its observed event stream."""
import contextlib
import json

from .client import Failure
from .evidence import utc_now


def contains_original(original, replayed):
    if isinstance(original, dict):
        return isinstance(replayed, dict) and all(key in replayed and contains_original(value, replayed[key])
                                                  for key, value in original.items())
    if isinstance(original, list):
        return isinstance(replayed, list) and len(original) == len(replayed) and all(
            contains_original(a, b) for a, b in zip(original, replayed))
    return type(original) is type(replayed) and original == replayed


def check_resubscribe(execution, entry, checks, session_id):
    source = execution.evidence.root / 'stream.jsonl'
    originals = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
    originals = [item for item in originals if item.get('phase') == entry['phase']]
    observed, sequences, content = False, False, False
    detail = 'original_stream_sequence_unavailable'
    try:
        original_seq = [int(item['sequence']) for item in originals]
        valid = (len(original_seq) > 1 and all(seq > 0 for seq in original_seq) and
                 original_seq == sorted(set(original_seq)) and
                 all(item['result'].get('taskId', item['result'].get('id')) == entry['task_id']
                     for item in originals))
    except (KeyError, TypeError, ValueError):
        valid = False
    if valid:
        cursor = original_seq[0]
        replay = []
        try:
            with contextlib.closing(execution.client.stream_rpc(
                    execution.conversation_selector(entry['conversation_id']),
                    {'id': entry['task_id'], 'afterSeq': cursor}, method='tasks/resubscribe')) as stream:
                for item in stream:
                    execution.evidence.append('resubscribe.jsonl', {'recorded_at': utc_now(),
                        'phase': entry['phase'], 'conversation_id': entry['conversation_id'], **item})
                    replay.append(item)
            observed = bool(replay)
            try:
                resumed_seq = [int(item['sequence']) for item in replay]
                sequences = (resumed_seq == original_seq[1:] and
                             all(item['result'].get('taskId', item['result'].get('id')) == entry['task_id'] and
                                 item['result'].get('contextId') == entry['conversation_id'] for item in replay))
                content = (sequences and all(contains_original(before['result'], after['result'])
                                             for before, after in zip(originals[1:], replay)) and
                           any(item['result'].get('final') is True for item in replay))
            except (KeyError, TypeError, ValueError):
                sequences, content = False, False
            detail = 'resumed_stream_mismatch'
        except Failure as error:
            if error.code in {'deadline_exceeded', 'transport_error'}:
                raise
            detail = 'resubscribe_' + error.code
    checks.extend([
        {'session': session_id, 'rule': '重新订阅获得任务事件', 'passed': observed,
         'detail': '' if observed else detail},
        {'session': session_id, 'rule': '续读事件序号完整且不重复', 'passed': sequences,
         'detail': '' if sequences else detail},
        {'session': session_id, 'rule': '续读保留原事件内容及结束状态', 'passed': content,
         'detail': '' if content else detail},
    ])
    execution.report['validation'] = {'checks': checks}
    execution.checkpoint()
