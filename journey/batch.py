"""Plan balanced runner groups and run isolated cases sequentially on each runner."""
import argparse
import json
import math
import os
import signal
import subprocess
import sys
from pathlib import Path

STAGE_SECONDS = {'prepare': 120, 'report': 60, 'publish': 120}
TERMINATION_GRACE = 10
CASE_OVERHEAD_MINUTES = math.ceil((sum(STAGE_SECONDS.values()) + 4 * (TERMINATION_GRACE + 5)) / 60)


def plan_batches(items, count):
    if type(count) is not int or count <= 0:
        raise ValueError('invalid_batch_jobs')
    if any(item['executor'] not in {'api', 'browser'} for item in items):
        raise ValueError('batch_requires_functional_cases')
    groups = [[] for _ in range(min(count, len(items)))]
    weights = [0] * len(groups)
    ordered = sorted(enumerate(items), key=lambda row: (-row[1]['step_timeout_minutes'], row[0]))
    for index, item in ordered:
        group = min(range(len(groups)), key=lambda i: (weights[i], i))
        groups[group].append((index, item))
        weights[group] += item['step_timeout_minutes'] + CASE_OVERHEAD_MINUTES
    result = []
    for index, members in enumerate(groups):
        members.sort()
        timeout = 15 + weights[index]
        if timeout > 360:
            raise ValueError('batch_exceeds_hosted_runner_limit')
        result.append({'case': f'group-{index + 1:02d}', 'executor': 'batch',
            'case_ids': [item['case'] for _, item in members],
            'needs_browser': any(item['executor'] == 'browser' for _, item in members),
            'job_timeout_minutes': timeout})
    return result


def stop_stage(process):
    # browser_case starts its worker in another session. Include descendant
    # groups so killing a stuck Python recorder cannot leave Chromium running.
    snapshot = subprocess.check_output(['ps', '-eo', 'pid=,ppid=,pgid='], text=True, timeout=5)
    entries = [tuple(map(int, line.split())) for line in snapshot.splitlines()]
    descendants = {process.pid}
    while True:
        found = {pid for pid, parent, _ in entries if parent in descendants}
        if found.issubset(descendants):
            break
        descendants.update(found)
    groups = {process.pid} | {group for pid, _, group in entries if pid in descendants}
    if os.getpgrp() in groups:
        raise RuntimeError('stage_process_group_not_isolated')
    def send(signum):
        for group in groups:
            try:
                os.killpg(group, signum)
            except ProcessLookupError:
                pass  # A process that already exited needs no further signal.
    send(signal.SIGTERM)
    try:
        process.wait(timeout=TERMINATION_GRACE)
    except subprocess.TimeoutExpired:
        pass
    finally:
        send(signal.SIGKILL)
    return process.wait()


def run_stage(command, cwd, env, timeout):
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'exit_code': stop_stage(process), 'timed_out': True}
    except BaseException:
        stop_stage(process)
        raise
    return {'exit_code': code, 'timed_out': False}


def run_case(case, workspace, directory, public_root, repo, env):
    from .catalog import case_executor, step_timeout_minutes
    from .evidence import Evidence, configured_secrets

    directory.mkdir(parents=True, exist_ok=False)
    case_id = case['id']
    child_env = dict(env)
    child_env['PYTHONPATH'] = str(repo) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    child_env['GITHUB_STEP_SUMMARY'] = str(directory / 'case-step-summary.md')
    if case_executor(case) != 'browser':
        for name in ('MOI_UI_ACCOUNT', 'MOI_UI_PASSWORD', 'MOI_UI_BASE_URL', 'MOI_UI_AUTH_ORIGIN'):
            child_env.pop(name, None)
    evidence = Evidence(directory / 'results' / case_id, configured_secrets())
    stages = {}

    def stage(name, command, timeout):
        print(f'::group::{case_id} / {name}', flush=True)
        try:
            stages[name] = run_stage(command, directory, child_env, timeout)
        except (OSError, ValueError) as error:
            stages[name] = {'exit_code': None, 'timed_out': False, 'error': type(error).__name__}
        finally:
            print('::endgroup::', flush=True)
        evidence.write('batch-stages.json', {'case_id': case_id, 'stages': stages})
        return stages[name]['exit_code'] == 0 and not stages[name]['timed_out']

    prepared = stage('prepare', [sys.executable, '-m', 'journey.catalog', 'prepare', case_id], STAGE_SECONDS['prepare'])
    if prepared:
        module = 'journey.browser_case' if case_executor(case) == 'browser' else 'journey.functional_case'
        stage('execute', [sys.executable, '-m', module, case_id], step_timeout_minutes(case) * 60)
    stage('report', [sys.executable, '-m', 'journey.report', 'case', case_id], STAGE_SECONDS['report'])
    if stage('publish', [sys.executable, '-m', 'journey.publish'], STAGE_SECONDS['publish']):
        # Only expose a complete, scanned case; failed publication cannot leave
        # a plausible but incomplete result for the aggregate report to accept.
        public_root.mkdir(parents=True, exist_ok=True)
        (directory / 'published-results' / case_id).rename(public_root / case_id)
        Evidence(public_root / case_id, configured_secrets()).write('batch-stages.json',
                                                                  {'case_id': case_id, 'stages': stages})
    result_path = evidence.root / 'result.json'
    try:
        result = json.loads(result_path.read_text())
    except (OSError, ValueError):
        result = {}
    failed = [name for name, value in stages.items() if value['exit_code'] != 0 or
              value['timed_out'] or value.get('error')]
    return {'case_id': case_id, 'workspace': workspace, 'failed_stages': failed,
            'test_status': result.get('test_status'), 'functional': result.get('functional'),
            'task': result.get('task'), 'task_seconds': result.get('task_seconds'),
            'stages': stages, 'directory': str(directory)}


def write_summary(rows, destination, env):
    from .evidence import Evidence, configured_secrets
    Evidence(destination, configured_secrets()).write('batch-result.json', {'cases': rows})
    lines = ['## Sequential case group', '',
             '| Case | 框架步骤异常 | 功能验收 | 任务耗时（秒） |',
             '|---|---|---|---:|']
    for row in rows:
        lines.append(f'| {row["case_id"]} | {", ".join(row["failed_stages"]) or "无"} | '
                     f'{(row.get("functional") or {}).get("state", "未知")} | '
                     f'{row.get("task_seconds") if row.get("task_seconds") is not None else "—"} |')
    for row in rows:
        path = Path(row['directory']) / 'results' / row['case_id'] / 'case-summary.md'
        if path.exists():
            lines += ['', f'<details><summary>{row["case_id"]} 单 case 报告</summary>', '',
                      path.read_text(), '', '</details>']
    summary = env.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as output:
            output.write('\n'.join(lines) + '\n')


def run_batch(case_ids, workspace, directory, public_root, env):
    from .catalog import ROOT, case_executor, load_case, load_suite
    suite = load_suite(workspace)
    if (not isinstance(case_ids, list) or not case_ids or
            any(not isinstance(value, str) or value not in suite['cases'] for value in case_ids) or
            len(set(case_ids)) != len(case_ids)):
        raise ValueError('invalid_batch_case_ids')
    cases = [load_case(case_id) for case_id in case_ids]
    if any(case['workspace'] != workspace or case_executor(case) not in {'api', 'browser'} for case in cases):
        raise ValueError('batch_requires_functional_cases')
    if any((public_root / case_id).exists() for case_id in case_ids):
        raise ValueError('batch_publication_already_exists')
    rows = []
    try:
        for case in cases:
            try:
                row = run_case(case, workspace, directory / case['id'], public_root, ROOT, env)
            except Exception as error:
                # Keep collecting the remaining cases, but never mask a harness failure.
                row = {'case_id': case['id'], 'workspace': workspace, 'directory': str(directory / case['id']),
                       'failed_stages': ['orchestration'], 'error': type(error).__name__}
                print(f'::error::{case["id"]} orchestration failed ({type(error).__name__})', flush=True)
            rows.append(row)
            write_summary(rows, directory, {**env, 'GITHUB_STEP_SUMMARY': ''})
    finally:
        write_summary(rows, directory, env)
    return 1 if any(row['failed_stages'] for row in rows) else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    def stop(_signum, _frame):
        raise KeyboardInterrupt('workflow_canceled')
    signal.signal(signal.SIGTERM, stop)
    return run_batch(json.loads(args.cases), args.workspace, Path(args.work).resolve(), Path(args.out).resolve(), os.environ.copy())


if __name__ == '__main__':
    raise SystemExit(main())
