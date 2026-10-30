"""Clean exact case-owned files after recording evidence and before archiving."""
import ast
import json
import shlex
import time

from .client import Failure, error_record, segment
from .evidence import Evidence
from .hello import TERMINAL
from .run_case import Execution


def same_cleanup_command(actual, expected):
    try:
        left, right = shlex.split(actual), shlex.split(expected)
        return (len(left) == len(right) == 3 and left[:2] == right[:2] == ['python3', '-c'] and
                ast.dump(ast.parse(left[2])) == ast.dump(ast.parse(right[2])))
    except (TypeError, ValueError, SyntaxError):
        return False


def cleanup_sandbox(journey, conversation_id):
    parent = journey.execution
    events = parent.evidence.root / 'events.jsonl'
    tasks = {task['task_id'] for task in parent.tasks if task['conversation_id'] == conversation_id}
    def sandbox_used(line):
        event = json.loads(line)
        return (event.get('task_id') in tasks and isinstance(event.get('payload'), dict) and
                event['payload'].get('tool_id') == 'mcp__moi-tools__bash')
    if any(not task.get('capture_complete') for task in parent.tasks if task['conversation_id'] == conversation_id):
        raise Failure('native_sandbox_usage_unconfirmed')
    if not events.exists() or not any(sandbox_used(line) for line in events.read_text().splitlines()):
        return {'status': 'not_used'}
    path = journey.temp_dir
    code = ("import json, pathlib, shutil\np=pathlib.Path(" + repr(path) + ")\n"
            "if p.is_symlink(): p.unlink()\nelif p.exists(): shutil.rmtree(p)\n"
            "print(json.dumps({'path':str(p),'exists':p.exists() or p.is_symlink()}))\n")
    command = 'python3 -c ' + shlex.quote(code)
    evidence = Evidence(parent.evidence.root / 'cleanup' / conversation_id, parent.evidence.secrets)
    child = Execution(parent.client, parent.case, evidence, parent.work, parent.model)
    child.root, child.selector = parent.root, parent.conversation_selector(conversation_id)
    child.conversations.append({'id': conversation_id, 'selector': child.selector, 'purpose': 'cleanup'})
    record = {'status': 'running', 'path': path, 'tasks': child.tasks}
    journey.state.setdefault('sandbox_cleanup', {})[conversation_id] = record
    parent.checkpoint()
    deadline, observer = parent.client.deadline, parent.client.observer
    parent.client.deadline = min(deadline - 20, time.monotonic() + 45)
    parent.client.observer = evidence.observe_transport
    try:
        _, task = child.send(conversation_id, 'cleanup:sandbox',
            '本次业务测试结束。现在只做文件清理：用 bash 工具原样执行下面这条命令一次，删除本次会话专用临时目录。'
            '不要改任何数据库，不要重新执行业务操作，不要删除其他目录。\n' + command)
        calls, outputs = {}, {}
        for line in (evidence.root / 'events.jsonl').read_text().splitlines():
            event = json.loads(line); payload = event.get('payload')
            if not isinstance(payload, dict):
                continue
            if event.get('task_id') != task['id'] or payload.get('tool_id') != 'mcp__moi-tools__bash':
                continue
            if event.get('kind') == 'tool_call':
                calls[payload['call_id']] = payload.get('arguments', {}).get('command')
            elif event.get('kind') == 'tool_result':
                outputs[payload['call_id']] = payload
        matched = [key for key, value in calls.items() if same_cleanup_command(value, command)]
        result = outputs.get(matched[0], {}) if len(matched) == len(calls) == 1 else {}
        wrapper = result.get('output', {})
        output = wrapper.get('output', {}) if isinstance(wrapper, dict) else {}
        valid = result.get('success') is True and output.get('exit_code') == 0
        if valid:
            try:
                valid = json.loads(output['stdout']) == {'path': path, 'exists': False}
            except (KeyError, ValueError):
                valid = False
        if not valid:
            raise Failure('native_sandbox_cleanup_not_verified')
        record['status'] = 'deleted'
    finally:
        # Cleanup has its own bounded task cancellation and evidence.
        try:
            parent.client.deadline = time.monotonic() + 35
            child.cleanup_tasks()
            record['tasks_confirmed'] = child.pending_conversation is None and all(t.get('state') in TERMINAL for t in child.tasks)
            if child.report['harness_errors']:
                record['errors'] = child.report['harness_errors']
                record['status'] = 'incomplete'
            child.checkpoint()
        finally:
            parent.client.deadline, parent.client.observer = deadline, observer
            parent.checkpoint()


def delete_owned_file(execution, file_id, conversation_id, owned_tasks):
    file_path = execution.root + '/files/' + segment(file_id)
    settle_deadline = min(execution.client.deadline, time.monotonic() + 10)
    while True:
        try:
            file = execution.client.product('GET', file_path)
        except Failure as error:
            if error.code == 'http_error' and error.http_status == 404:
                return
            raise
        execution.evidence.append('cleanup-file-state.jsonl', file)
        if (file.get('file_id') != file_id or file.get('source_session_id') != conversation_id or
                file.get('source_task_id') not in owned_tasks):
            raise Failure('native_output_file_identity_or_reference_mismatch')
        # RefCount is omitted by the product DTO when zero. KB/placement cleanup
        # can still be settling; only read while references remain, never force it.
        references = file.get('ref_count', 0)
        if references == 0:
            break
        if type(references) is not int or references < 0 or time.monotonic() >= settle_deadline:
            raise Failure('native_output_file_identity_or_reference_mismatch', details={'ref_count': references})
        time.sleep(min(1, max(0, settle_deadline - time.monotonic())))
    execution.client.request('DELETE', file_path, binary=True)
    try:
        execution.client.request('GET', file_path)
    except Failure as error:
        if error.code == 'http_error' and error.http_status == 404:
            return
        raise
    raise Failure('native_output_file_delete_unconfirmed')


def cleanup_artifacts(journey, conversation_id):
    execution = journey.execution
    inventory = execution.evidence.root / 'artifacts' / conversation_id / 'inventory.json'
    artifacts = json.loads(inventory.read_text())
    selector = execution.conversation_selector(conversation_id)
    owned_tasks = {task['task_id'] for task in execution.tasks if task['conversation_id'] == conversation_id}
    records = journey.state.setdefault('file_cleanup', [])
    for item in artifacts:
        if (item.get('conversation_id') != conversation_id or item.get('task_id') not in owned_tasks or
                item.get('agent_id') != selector['agent_id'] or item.get('agent_workspace_id') != selector['agent_workspace_id']):
            raise Failure('native_artifact_cleanup_identity_unverified')
    if not artifacts:
        return
    refs = [{key: item[key] for key in ('artifact_id', 'version')} for item in artifacts]
    result = execution.client.product('POST', execution.root + '/agent-artifacts/batch-delete', {'items': refs})
    execution.evidence.write('artifact-cleanup-' + conversation_id + '.json', result)
    expected = {(ref['artifact_id'], ref['version']) for ref in refs}
    succeeded = result.get('succeeded', [])
    if (len(succeeded) != len(refs) or {(ref.get('artifact_id'), ref.get('version')) for ref in succeeded} != expected or
            result.get('failed') != []):
        raise Failure('native_artifact_delete_unconfirmed')
    errors = []
    for file_id in sorted({item['file_id'] for item in artifacts}):
        try:
            delete_owned_file(execution, file_id, conversation_id, owned_tasks)
            records.append({'file_id': file_id, 'status': 'deleted'})
        except Exception as error:
            errors.append(error_record('cleanup_file', error, file_id=file_id))
        execution.checkpoint()
    if errors:
        raise Failure('native_files_cleanup_incomplete', details={'errors': errors})
