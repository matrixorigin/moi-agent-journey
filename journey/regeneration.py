"""Exercise the public A2A regenerate control and verify task lineage."""

import json
import time
import uuid
from urllib.parse import urlencode

from .builder_flow import ProductCaseFailure
from .client import Failure, segment
from .evidence import utc_now
from .hello import TERMINAL, task_state
from .message_controls import conversation_task_ids


def regenerate(execution, conversation_id, session_id, phase, source, checks):
    selector = execution.conversation_selector(conversation_id)

    def record(rule, passed, detail):
        checks.append({'session': session_id, 'rule': rule, 'passed': bool(passed),
                       'detail': '' if passed else detail})
        execution.report['validation'] = {'checks': checks}
        execution.checkpoint()

    source_id = source.get('task_id') if isinstance(source, dict) else None
    if not isinstance(source_id, str) or not source_id:
        raise Failure('regeneration_source_missing')
    path = execution.root + '/agent-runtime-tasks/' + segment(source_id)
    query = urlencode(selector)
    before = execution.client.product('GET', path + '?' + query)
    known = {item['task_id'] for item in execution.tasks if item.get('conversation_id') == conversation_id}
    source_ok = (source.get('state') == 'completed' and before.get('id') == source_id and
                 before.get('context_id') == conversation_id and before.get('state') == 'completed' and
                 all(isinstance(before.get(key), str) and before[key] for key in
                     ('manifest_id', 'trace_id', 'turn_input_snapshot_id', 'turn_output_snapshot_id')) and
                 before.get('turn_output_committed') is True and source_id in known and
                 conversation_task_ids(execution, conversation_id) == known)
    record('重新生成的源任务可回读', source_ok, 'regeneration_source_mismatch')
    if not source_ok:
        raise ProductCaseFailure('regeneration_source_mismatch')

    control_id, message_id = str(uuid.uuid4()), str(uuid.uuid4())
    control = {'type': 'moi.run.control', 'version': 1, 'controlId': control_id, 'action': 'regenerate',
               'target': {'agentId': selector['agent_id'], 'contextId': conversation_id, 'taskId': source_id},
               'rerunMode': 'same_manifest', 'conversationMode': 'replace_view', 'attempt': 2}
    params = {'message': {'kind': 'message', 'role': 'user', 'messageId': message_id,
              'taskId': source_id, 'contextId': conversation_id, 'parts': [{'kind': 'data', 'data': control}]}}
    execution.evidence.append('dialogue.jsonl', {'at': utc_now(), 'phase': phase, 'conversation_id': conversation_id,
                                               'message_id': message_id, 'control': control})
    execution.pending_conversation = conversation_id
    execution.checkpoint()
    started, started_at = time.monotonic(), utc_now()
    try:
        receipt = execution.client.rpc(selector, 'message/send', params)
    except Failure as error:
        if ((error.code == 'http_error' and type(error.http_status) is int and 400 <= error.http_status < 500
             and error.http_status not in {401, 403}) or error.code == 'rpc_error'):
            record('重新生成请求被接受', False, error.code + ':' + str(error.http_status))
            if conversation_task_ids(execution, conversation_id) == known:
                execution.pending_conversation = None
                execution.checkpoint()
            raise ProductCaseFailure('regeneration_rejected') from None
        raise
    execution.evidence.write('sessions/' + session_id + '/regeneration-receipt.json', receipt)
    new_task = receipt.get('task')
    task_id = new_task.get('id') if isinstance(new_task, dict) else None
    distinct = isinstance(task_id, str) and bool(task_id) and task_id not in known
    record('重新生成产生独立任务', distinct, 'regenerated_task_missing_or_duplicate')
    if not distinct:
        if conversation_task_ids(execution, conversation_id) == known:
            execution.pending_conversation = None
            execution.checkpoint()
        raise ProductCaseFailure('regenerated_task_missing_or_duplicate')

    entry = {'task_id': task_id, 'conversation_id': conversation_id, 'message_id': message_id, 'phase': phase,
             'started_at': started_at, 'state': 'working', 'event_cursor': 0, 'event_count': 0,
             'event_kinds': {}, 'capture_complete': False}
    execution.tasks.append(entry)
    execution.pending_conversation = None
    execution.checkpoint()
    parts = receipt.get('parts')
    data = parts[0].get('data') if (isinstance(parts, list) and len(parts) == 1 and
            isinstance(parts[0], dict) and parts[0].get('kind') == 'data') else None
    receipt_ok = (receipt.get('kind') == 'message' and receipt.get('role') == 'agent' and
                  receipt.get('messageId') == message_id and receipt.get('contextId') == conversation_id and
                  isinstance(data, dict) and data.get('type') == 'moi.run.control.result' and
                  data.get('version') == 1 and
                  data.get('controlId') == control_id and data.get('status') == 'created' and
                  data.get('action') == 'regenerate' and data.get('taskId') == task_id and
                  data.get('contextId') == conversation_id)
    record('重新生成返回对应的控制回执', receipt_ok, 'regeneration_receipt_mismatch')

    task = execution.client.rpc(selector, 'tasks/get', {'id': task_id})
    state = task_state(task, conversation_id, task_id)
    while state not in TERMINAL:
        if time.monotonic() >= execution.client.deadline:
            raise Failure('deadline_exceeded')
        entry['state'] = state
        execution.capture(entry)
        time.sleep(min(1, max(0, execution.client.deadline - time.monotonic())))
        task = execution.client.rpc(selector, 'tasks/get', {'id': task_id})
        state = task_state(task, conversation_id, task_id)
    entry['state'], entry['seconds'] = state, round(time.monotonic() - started, 3)
    execution.evidence.write('tasks/' + task_id + '/a2a.json', task)
    execution.capture(entry, final=True)
    if state != 'completed':
        raise Failure('agent_' + state, details={'task_id': task_id, 'runtime_error': entry.get('runtime_error')})

    runtime = json.loads((execution.evidence.root / 'tasks' / task_id / 'runtime.json').read_text())
    attempt = runtime.get('attempt')
    lineage_ok = (isinstance(attempt, dict) and attempt.get('attempt') == 2 and
                  attempt.get('retry_of_task_id') == source_id and attempt.get('same_manifest') is True and
                  attempt.get('regenerate') is True and runtime.get('manifest_id') == before.get('manifest_id') and
                  bool(runtime.get('trace_id')) and runtime.get('trace_id') != before.get('trace_id') and
                  runtime.get('turn_output_committed') is True and
                  all(isinstance(runtime.get(key), str) and runtime[key] and runtime[key] != before.get(key)
                      for key in ('turn_input_snapshot_id', 'turn_output_snapshot_id')))
    record('新任务保留源任务关系并使用独立 trace', lineage_ok, 'regeneration_lineage_mismatch')
    after = execution.client.product('GET', path + '?' + query)
    fields = ('id', 'context_id', 'state', 'manifest_id', 'trace_id', 'turn_input_snapshot_id',
              'turn_output_snapshot_id', 'turn_output_committed')
    source_unchanged = all(before.get(field) == after.get(field) for field in fields)
    record('源任务及其输出未被覆盖', source_unchanged, 'regeneration_source_changed')
    execution.evidence.write('sessions/' + session_id + '/regeneration-lineage.json',
                             {'source_before': before, 'source_after': after, 'regenerated': runtime})
    return entry, task
