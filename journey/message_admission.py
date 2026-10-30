"""Replay one request and challenge an occupied conversation through public A2A."""
import copy
import json
import shlex
import time
import uuid
from urllib.parse import urlencode

from .a2a import final_reply
from .builder_flow import ProductCaseFailure
from .client import Client, Failure, error_record
from .evidence import utc_now
from .hello import TERMINAL, task_state
from .message_controls import conversation_task_ids
from .native_skill_cleanup import cleanup_artifacts, cleanup_sandbox, same_python_command


def rpc_rejection(exchange):
    """Only a matching structured runtime rejection can prove non-admission."""
    response = exchange.get('response') or {}
    try:
        envelope = json.loads(response['error_body']) if 'error_body' in response else response
    except (ValueError, TypeError):
        return None
    error = envelope.get('error') if isinstance(envelope, dict) else None
    if (not isinstance(error, dict) or envelope.get('jsonrpc') != '2.0' or
            envelope.get('id') != (exchange.get('request') or {}).get('id')):
        return None
    data = error.get('data') or {}
    if (not isinstance(data, dict) or data.get('domain') != 'moi-core.agent_runtime' or
            (error.get('code'), data.get('reason')) not in {
                (-32603, 'INTERNAL'), (-32006, 'CONFLICT'), (-32006, 'CONVERSATION_BUSY')}):
        return None
    return error


def command_outputs(events, task_id, command):
    calls, results = {}, {}
    for line in events.read_text().splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict) or payload.get('tool_id') != 'mcp__moi-tools__bash':
            continue
        call_id = payload.get('call_id')
        if not isinstance(call_id, str) or not call_id:
            continue
        if event.get('kind') == 'tool_call':
            arguments = payload.get('arguments')
            calls[call_id] = arguments.get('command') if isinstance(arguments, dict) else None
        elif event.get('kind') == 'tool_result':
            results[call_id] = payload
    matching = [key for key, value in calls.items() if same_python_command(value, command)]
    result = results.get(matching[0], {}) if len(matching) == 1 else {}
    wrapper = result.get('output', {})
    output = wrapper.get('output', {}) if isinstance(wrapper, dict) else {}
    return (len(calls) == len(matching) == 1 and result.get('success') is True and
            output.get('exit_code') == 0 and output.get('output_truncated') is not True), output.get('stdout')


class MessageAdmissionJourney:
    def __init__(self, execution, mode, checks):
        if mode not in {'replay', 'busy'}:
            raise Failure('invalid_message_admission_case')
        self.execution, self.mode, self.checks = execution, mode, checks
        self.session_id = execution.case['sessions'][0]['id']
        self.temp_dir = '/tmp/j2-ingress-' + execution.report['run_id'].replace('-', '')
        self.state = {'mode': mode, 'cleanup': 'pending', 'requests': []}
        execution.report['message_admission'] = self.state
        self.original = None
        self.expected = {}

    def prepare(self):
        self.code = 'WRITE-' + self.execution.report['run_id'][:8]
        path = self.temp_dir + '/writes.txt'
        write = ("from pathlib import Path\np=Path(" + repr(path) + ")\n"
                 "p.parent.mkdir(parents=True,exist_ok=True)\n"
                 "with p.open('a') as f: f.write(" + repr(self.code + '\n') + ")\n"
                 "print(p.read_text())\n")
        verify = ("from pathlib import Path\nimport json\np=Path(" + repr(path) + ")\n"
                  "lines=p.read_text().splitlines()\np.unlink()\n"
                  "print(json.dumps({'writes':len(lines),'deleted':not p.exists()}))\n")
        busy = "import time\ntime.sleep(3)\nprint(" + repr(self.code) + ")\n"
        self.command = 'python3 -c ' + shlex.quote(write if self.mode == 'replay' else busy)
        self.verify_command = 'python3 -c ' + shlex.quote(verify)
        return {'admission_command': self.command, 'admission_verify_command': self.verify_command,
                'admission_code': self.code}

    def record(self, rule, passed, reason, *, required=False):
        self.checks.append({'session': self.session_id, 'rule': rule, 'passed': bool(passed),
                            'detail': '' if passed else reason})
        self.execution.report['validation'] = {'checks': self.checks}
        self.execution.checkpoint()
        if required and not passed:
            raise ProductCaseFailure(reason)

    def control(self, params, stage):
        parent = self.execution.client
        exchanges = []
        client = Client(parent.base_url, parent.headers['X-API-Key'],
                        timeout=max(.1, parent.deadline - time.monotonic()), observer=exchanges.append)
        client.headers = dict(parent.headers)
        cid = params['message']['contextId']
        self.execution.pending_conversation = cid
        self.execution.checkpoint()
        try:
            response = client.rpc(self.execution.conversation_selector(cid), 'message/send', params)
            error = None
        except Failure as failure:
            if failure.code != 'rpc_error' and not (failure.code == 'http_error' and failure.http_status not in {None, 401, 403}):
                raise
            response, error = None, error_record(stage, failure)
        finally:
            parent.requests += client.requests
            for exchange in exchanges:
                if parent.observer:
                    parent.observer(exchange)
                self.execution.evidence.append('message-admission-http.jsonl', {'stage': stage, **exchange})
        self.state['requests'].append({'stage': stage, 'request': params, 'response': response, 'error': error})
        if error and exchanges:
            rejection = rpc_rejection(exchanges[-1])
            error['trace_headers'] = exchanges[-1].get('headers', {})
            if rejection:
                error['details'] = rejection
                self.execution.pending_rejection = {'conversation_id': cid, 'phase': stage,
                    'error': rejection, 'trace_headers': error['trace_headers']}
        if response and response.get('kind') == 'task':
            state = task_state(response, cid)
            task_id = response.get('id')
            if task_id not in {task['task_id'] for task in self.execution.tasks}:
                entry = {'task_id': task_id, 'conversation_id': cid, 'message_id': params['message']['messageId'],
                         'phase': 'probe:' + stage, 'started_at': utc_now(), 'state': state,
                         'event_cursor': 0, 'event_count': 0, 'event_kinds': {}, 'capture_complete': False}
                self.execution.tasks.append(entry)
                self.execution.capture(entry, final=state in TERMINAL)
            known = {task['task_id'] for task in self.execution.tasks if task['conversation_id'] == cid}
            if conversation_task_ids(self.execution, cid) == known:
                self.execution.pending_conversation = None
        self.execution.checkpoint()
        return response, error

    def admitted(self, entry, params):
        self.original = copy.deepcopy(params)
        cid, task_id = entry['conversation_id'], entry['task_id']
        task = self.execution.client.rpc(self.execution.conversation_selector(cid), 'tasks/get', {'id': task_id})
        active = task_state(task, cid, task_id) in {'submitted', 'working'}
        if not active:
            self.execution.report['blocked'] = True
        self.record('并发探测前原任务已受理且尚未结束', active, 'task_finished_before_admission_probe', required=True)
        probe = copy.deepcopy(params)
        if self.mode == 'busy':
            probe['message']['messageId'] = probe['idempotencyKey'] = str(uuid.uuid4())
            probe['message']['parts'] = [{'kind': 'text', 'text': '只回复 REJECTED-' + str(uuid.uuid4())}]
        response, error = self.control(probe, 'during-run')
        if self.mode == 'replay':
            self.record('运行中重放同一消息返回原任务', response is not None and response.get('kind') == 'task' and
                response.get('id') == task_id and response.get('contextId') == cid,
                'inflight_replay_did_not_return_original_task')
        else:
            details = (error or {}).get('details') or {}
            rejected = (error is not None and ((error.get('reason') == 'rpc_error' and
                details.get('code') == -32006 and (details.get('data') or {}).get('reason') in {'CONFLICT', 'CONVERSATION_BUSY'}) or
                error.get('http_status') == 409))
            self.record('不同消息在会话忙碌时明确返回冲突', rejected, 'busy_message_not_rejected_as_conflict')
        self.record('运行中探测没有额外创建任务', conversation_task_ids(self.execution, cid) == {task_id},
                    'admission_probe_created_additional_task')

    def ledger(self, cid):
        expected = set(self.expected)
        self.record('会话任务恰好对应本次正常发送轮次', conversation_task_ids(self.execution, cid) == expected,
                    'admission_task_ledger_mismatch')
        scope = self.execution.conversation_selector(cid)
        results = {}
        for role in ('user', 'agent'):
            path = self.execution.root + '/conversations/' + cid + '/messages?' + urlencode({
                **scope, 'role': role, 'limit': 100, 'offset': 0})
            results[role] = self.execution.client.product('GET', path)
        self.execution.evidence.write('sessions/' + self.session_id + '/admission-history-' + str(len(expected)) + '.json', results)
        valid = True
        for role, page in results.items():
            items = page.get('items')
            valid = valid and isinstance(items, list) and page.get('total') == len(expected) and len(items) == len(expected)
            if not valid:
                break
            valid = (all(item.get('conversation_id') == cid and item.get('role') == role for item in items) and
                     len({item.get('id') for item in items}) == len(items) and
                     {item.get('task_id') for item in items} == expected)
            if role == 'user':
                valid = valid and all(any(part.get('kind') == 'text' and part.get('text') == self.expected[item['task_id']]
                    for part in item.get('parts', [])) for item in items)
        self.record('重放或被拒消息不重复写入对话历史', valid, 'admission_message_history_mismatch')

    def confirm_control(self, cid):
        if self.execution.pending_conversation == cid:
            self.execution.cleanup_tasks()
            if self.execution.pending_conversation is not None:
                raise Failure('probe_admission_outcome_unconfirmed')

    def finish(self, cid, entry, task, prompt, stage):
        self.expected[entry['task_id']] = prompt
        self.confirm_control(cid)
        if stage == 'probe':
            if self.mode == 'replay':
                before = json.loads((self.execution.evidence.root / 'tasks' / entry['task_id'] / 'runtime.json').read_text())
                event_count = self.tool_events(entry['task_id'])
                response, _ = self.control(self.original, 'after-completion')
                self.confirm_control(cid)
                self.record('完成后重放返回同一任务及原答复', response is not None and response.get('id') == entry['task_id'] and
                    response.get('contextId') == cid and response.get('status', {}).get('state') == 'completed' and
                    final_reply(response) == final_reply(task), 'completed_replay_result_mismatch')
                self.execution.capture(entry, final=True)
                after = json.loads((self.execution.evidence.root / 'tasks' / entry['task_id'] / 'runtime.json').read_text())
                self.record('重放保留原 trace、运行快照且不重复调用工具', all(before.get(key) == after.get(key) for key in
                    ('id', 'trace_id', 'manifest_id', 'turn_input_snapshot_id', 'turn_output_snapshot_id')) and
                    self.tool_events(entry['task_id']) == event_count, 'replay_changed_runtime_or_tool_calls')
            valid, stdout = command_outputs(self.execution.evidence.root / 'events.jsonl', entry['task_id'], self.command)
            self.record('原任务准确执行一次指定工具命令', valid and isinstance(stdout, str) and stdout.strip() == self.code,
                        'admission_original_command_not_once')
        elif stage == 'followup' and self.mode == 'replay':
            valid, stdout = command_outputs(self.execution.evidence.root / 'events.jsonl', entry['task_id'], self.verify_command)
            try:
                valid = valid and json.loads(stdout) == {'writes': 1, 'deleted': True}
            except (ValueError, TypeError):
                valid = False
            self.record('重试后写入仅发生一次且本次文件已删除', valid, 'replay_write_count_or_cleanup_mismatch')
        elif stage != 'followup':
            raise Failure('invalid_admission_check')
        self.ledger(cid)

    def tool_events(self, task_id):
        return [event for line in (self.execution.evidence.root / 'events.jsonl').read_text().splitlines()
                if (event := json.loads(line)).get('task_id') == task_id and event.get('kind') in {'tool_call', 'tool_result'}]

    def cleanup_outputs(self, cid):
        try:
            if self.mode == 'replay':
                result = cleanup_sandbox(self, cid)
                record = self.state.get('sandbox_cleanup', {}).get(cid)
                confirmed = (record.get('status') == 'deleted' and record.get('tasks_confirmed') is True
                             if record is not None else result == {'status': 'not_used'})
                if not confirmed:
                    raise Failure('admission_sandbox_cleanup_unconfirmed')
            cleanup_artifacts(self, cid)
            self.state['cleanup'] = 'completed'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_message_admission', error))
        self.execution.checkpoint()
