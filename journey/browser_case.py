"""Run a browser product case, capture its real QA tasks, and clean up test data."""
import argparse
import json
import os
import signal
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from .catalog import ROOT, load_case
from .agent_builder import candidate_from_task, cleanup_candidate
from .client import Client, Failure, segment
from .evidence import Evidence, configured_secrets, utc_now
from .hello import TERMINAL, discover, task_state
from .run_case import Execution


def task_duration(runtime):
    try:
        start = datetime.fromisoformat(runtime['created_at'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(runtime['updated_at'].replace('Z', '+00:00'))
        return max(0, round((end - start).total_seconds(), 3))
    except (KeyError, TypeError, ValueError):
        return None


def stop_process(process):
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def fetch_tasks(execution, observations, deadline):
    """Match each browser conversation independently to its persisted runtime task."""
    client = execution.client
    records = [item for item in observations if item.get('kind') == 'task_request']
    prepared, phases = [], set()
    for record in records:
        phase, cid = record.get('phase'), record.get('conversation_id')
        selector = {key: record.get(key) for key in ('agent_id', 'agent_workspace_id')}
        if (phase not in {'create', 'verify'} or phase in phases or
                not isinstance(cid, str) or not cid or
                any(not isinstance(value, str) or not value for value in selector.values())):
            execution.report['harness_errors'].append({'stage': 'browser_task',
                                                        'reason': 'invalid_browser_task_identity'})
            continue
        phases.add(phase)
        conversation = {'id': cid, 'purpose': 'browser:' + phase, 'cleanup': 'task_not_observed',
                        'selector': selector}
        execution.conversations.append(conversation)
        prepared.append((record, selector, conversation))
    execution.checkpoint()
    for record, selector, conversation in prepared:
        phase, cid = record['phase'], record['conversation_id']
        try:
            query = urlencode({**selector, 'context_id': cid, 'limit': 100, 'offset': 0})
            find_until = min(deadline, time.monotonic() + 15)
            while True:
                result = client.product('GET', execution.root + '/agent-runtime-tasks?' + query)
                matches = [item for item in result['items'] if item.get('context_id') == cid and
                           item.get('agent_id') == selector['agent_id'] and
                           item.get('agent_workspace_id') == selector['agent_workspace_id']]
                if len(matches) > 1:
                    raise Failure('runtime_task_not_unique')
                if matches:
                    break
                if time.monotonic() >= find_until:
                    execution.report['failures'].append({'stage': 'browser:' + phase,
                                                         'reason': 'runtime_task_not_observed'})
                    break
                time.sleep(1)
            if not matches:
                continue
            item = matches[0]
            conversation['cleanup'] = 'pending'
            entry = {'task_id': item['id'], 'conversation_id': cid, 'message_id': record.get('message_id'),
                     'phase': 'browser:' + phase, 'state': item['a2a_state'], 'event_cursor': 0,
                     'event_count': 0, 'event_kinds': {}, 'capture_complete': False, 'selector': selector}
            execution.tasks.append(entry)
            execution.capture(entry, selector=selector)
            while True:
                task = client.rpc(selector, 'tasks/get', {'id': entry['task_id']})
                entry['state'] = task_state(task, cid, entry['task_id'])
                execution.evidence.write('tasks/' + entry['task_id'] + '/a2a.json', task)
                if entry['state'] in TERMINAL or entry['state'] in {'input-required', 'auth-required'}:
                    break
                if time.monotonic() >= deadline:
                    execution.report['status'] = 'timeout'
                    execution.report['failures'].append({'stage': entry['phase'], 'reason': 'agent_timeout'})
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            if entry['state'] in TERMINAL:
                execution.capture(entry, final=True, selector=selector)
            if entry['state'] != 'completed' and execution.report['status'] != 'timeout':
                execution.report['failures'].append({'stage': entry['phase'], 'reason': 'agent_' + entry['state']})
            runtime = json.loads((execution.evidence.root / 'tasks' / entry['task_id'] / 'runtime.json').read_text())
            entry['seconds'] = task_duration(runtime)
            if phase == 'create':
                execution.candidate = candidate_from_task(task, execution.agent_name, execution.workspace_id)
            execution.checkpoint()
        except Exception as error:
            execution.report['harness_errors'].append({'stage': 'capture_browser_task', 'conversation_id': cid,
                'reason': type(error).__name__ + ':' + str(error)})


def cleanup(execution):
    """Only archive objects identified by this run, after recording their events."""
    client = execution.client
    client.deadline = time.monotonic() + 100
    for task in execution.tasks:
        selector = task['selector']
        if task['state'] not in TERMINAL:
            try:
                stopped = client.rpc(selector, 'tasks/cancel', {'id': task['task_id']})
                task['state'] = task_state(stopped, task['conversation_id'], task['task_id'])
                limit = min(client.deadline - 20, time.monotonic() + 40)
                while task['state'] not in TERMINAL and time.monotonic() < limit:
                    time.sleep(1)
                    stopped = client.rpc(selector, 'tasks/get', {'id': task['task_id']})
                    task['state'] = task_state(stopped, task['conversation_id'], task['task_id'])
                if task['state'] not in TERMINAL:
                    raise Failure('cancel_not_confirmed')
            except Exception as error:
                execution.report['harness_errors'].append({'stage': 'cancel_task', 'task_id': task['task_id'],
                    'reason': type(error).__name__ + ':' + str(error)})
        if not task['capture_complete']:
            try:
                execution.capture(task, final=task['state'] in TERMINAL, selector=selector)
            except Exception as error:
                execution.report['harness_errors'].append({'stage': 'final_capture', 'task_id': task['task_id'],
                    'reason': type(error).__name__ + ':' + str(error)})
    for conversation in execution.conversations:
        try:
            if conversation['cleanup'] == 'task_not_observed' or any(
                    task['conversation_id'] == conversation['id'] and task['state'] not in TERMINAL
                    for task in execution.tasks):
                raise Failure('task_outcome_unknown')
            result = client.product('PATCH', execution.root + '/conversations/' + segment(conversation['id']) + '?' +
                                    urlencode(conversation['selector']), {'status': 'archived'})
            if result.get('status') != 'archived':
                raise Failure('archive_not_confirmed')
            conversation['cleanup'] = 'archived'
        except Exception as error:
            conversation['cleanup'] = 'incomplete'
            execution.report['harness_errors'].append({'stage': 'cleanup_conversation',
                'conversation_id': conversation['id'], 'reason': type(error).__name__ + ':' + str(error)})
    cleanup_candidate(execution)


def run(case, evidence, work):
    client = Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], timeout=case['timeout_seconds'],
                    observer=evidence.observe_transport)
    execution = Execution(client, case, evidence, work, case['model'])
    execution.agent_name = 'J2_' + uuid.uuid4().hex[:16]
    execution.candidate = None
    execution.commit = None
    process = None
    deadline = time.monotonic() + case['timeout_seconds']
    started = time.monotonic()
    try:
        execution.root, execution.selector = discover(client, case['workspace'])
        execution.workspace_id = execution.root.rsplit('/', 1)[-1]
        turns = case['sessions'][0]['turns']
        if [turn['phase'] for turn in turns] != ['create', 'verify']:
            raise Failure('invalid_browser_case')
        marker = 'JOURNEY_' + uuid.uuid4().hex
        config = {'workspaceId': execution.workspace_id, 'agentName': execution.agent_name, 'marker': marker,
                  'model': case['model'], 'createPrompt': turns[0]['prompt'].format(agent_name=execution.agent_name),
                  'verifyPrompt': turns[1]['prompt'].format(marker=marker),
                  'timeoutMs': max(1, int((deadline - time.monotonic()) * 1000))}
        observation_path = Path(work) / ('browser-' + execution.report['run_id'] + '.jsonl')
        observation_path.parent.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items() if not key.startswith('MOI_')}
        for key in ('MOI_UI_ACCOUNT', 'MOI_UI_PASSWORD', 'MOI_UI_BASE_URL', 'MOI_UI_AUTH_ORIGIN'):
            env[key] = os.environ[key]
        env['JOURNEY_WORKER_CONFIG'] = json.dumps(config, ensure_ascii=False)
        env['JOURNEY_OBSERVATIONS'] = str(observation_path.resolve())
        execution.report['stage'] = 'browser'
        execution.checkpoint()
        process = subprocess.Popen(['node', str(ROOT / 'browser' / 'worker.mjs')], cwd=ROOT, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        interrupted = None
        try:
            returncode = process.wait(timeout=max(1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stop_process(process)
            returncode = None
        except Failure as error:
            stop_process(process)
            returncode, interrupted = None, error.code
        observations = []
        if observation_path.exists():
            for line in observation_path.read_text().splitlines():
                try:
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError('invalid_observation')
                    observations.append(item)
                except (ValueError, TypeError):
                    execution.report['harness_errors'].append({'stage': 'browser', 'reason': 'invalid_observation'})
        for item in observations:
            evidence.append('browser.jsonl', item)
        results = [item for item in observations if item.get('kind') == 'result']
        commits = [item for item in observations if item.get('kind') == 'candidate_commit']
        if len(commits) > 1 or len(results) > 1:
            raise Failure('ambiguous_browser_result')
        execution.commit = commits[0] if commits else None
        if results:
            result = results[0]
            execution.report['status'] = result['status']
            execution.report['blocked'] = bool(result.get('blocked'))
            execution.report['validation'] = {'checks': result.get('checks', [])}
            if result.get('failure'):
                target = 'harness_errors' if result['status'] == 'error' else 'failures'
                execution.report[target].append(result['failure'])
        elif returncode is None and interrupted is None:
            execution.report['status'] = 'timeout'
            execution.report['failures'].append({'stage': 'browser', 'reason': 'case_timeout'})
        else:
            execution.report['status'] = 'error'
            execution.report['harness_errors'].append({'stage': 'browser',
                'reason': interrupted or 'browser_worker_did_not_report'})
        if returncode not in (0, None):
            execution.report['harness_errors'].append({'stage': 'browser', 'reason': 'browser_worker_failed'})
        if interrupted and results:
            execution.report['harness_errors'].append({'stage': 'browser', 'reason': interrupted})
        # Preserve already observed IDs even when the browser or product failed.
        client.deadline = max(client.deadline, time.monotonic() + 90)
        fetch_tasks(execution, observations, max(deadline, time.monotonic() + 20))
        created = results[0].get('created_agent') if results else None
        if created:
            query = urlencode({'agent_workspace_id': execution.workspace_id})
            agent = client.product('GET', execution.root + '/agents/' + segment(created['agent_id']) + '?' + query)
            verified = ((agent.get('id'), agent.get('name'), agent.get('workspace_id'), agent.get('status')) ==
                        (created['agent_id'], execution.agent_name, execution.workspace_id, 'active'))
            execution.report['sessions'].append({'id': 'browser', 'created_agent': {**created, 'verified': verified}})
            execution.report.setdefault('validation', {}).setdefault('checks', []).append(
                {'session': 'browser', 'rule': '创建结果可读取', 'passed': verified,
                 'detail': '' if verified else 'agent_readback_mismatch'})
            if not verified:
                execution.report['failures'].append({'stage': 'readback', 'reason': 'agent_readback_mismatch'})
        if execution.report['failures'] and execution.report['status'] == 'passed':
            execution.report['status'] = 'failed'
        execution.report['stage'] = 'complete'
    except Exception as error:
        execution.report['status'] = 'error'
        execution.report['harness_errors'].append({'stage': execution.report['stage'],
            'reason': type(error).__name__ + ':' + str(error)})
    finally:
        stop_process(process)
        execution.report['task_seconds'] = round(time.monotonic() - started, 3)
        execution.report['qa_seconds'] = execution.report['task_seconds']
        cleanup_started = time.monotonic()
        if execution.root:
            cleanup(execution)
        execution.report['cleanup_seconds'] = round(time.monotonic() - cleanup_started, 3)
        execution.report['capture_complete'] = bool(execution.tasks) and all(
            task['capture_complete'] for task in execution.tasks)
        execution.report['finished_at'] = utc_now()
        execution.checkpoint()
    return execution.report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case_id')
    parser.add_argument('--work', default='work')
    args = parser.parse_args()
    case = load_case(args.case_id)
    evidence = Evidence(Path('results') / case['id'], configured_secrets())
    def interrupted(_signum, _frame):
        raise Failure('runner_canceled')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result = run(case, evidence, args.work)
    except (KeyError, Failure) as error:
        evidence.write('result.json', {'case_id': case['id'], 'title': case['title'], 'workspace': case['workspace'],
            'status': 'error', 'blocked': True, 'tasks': [], 'failures': [],
            'harness_errors': [{'stage': 'configuration', 'reason': type(error).__name__}]})
        return 1
    print(json.dumps({'case_id': case['id'], 'status': result['status'], 'tasks': len(result['tasks']),
                      'harness_errors': len(result['harness_errors'])}))
    return 1 if result['harness_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
