"""Bounded case processes: each has its own signals, deadlines and work files."""
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import BinaryIO

from .batch import stop_stage, write_summary, CASE_OVERHEAD_MINUTES
from .catalog import ROOT, load_case, load_suite, step_timeout_minutes


PROGRESS_INTERVAL_SECONDS = 30
TASK_OUTCOMES = {
    'completed': 'delivered',
    'abnormal': 'agent error',
    'timeout': 'agent timeout',
    'canceled': 'agent canceled',
    'not_delivered': 'no delivery',
    'unknown': 'outcome unavailable',
}


@dataclass
class RunningCase:
    case_id: str
    process: subprocess.Popen
    work: Path
    output: BinaryIO
    started: float
    deadline: float


def case_outcome(public_root, case_id, failed_stages):
    if failed_stages:
        return 'framework error'
    try:
        result = json.loads((public_root / case_id / 'result.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'report unavailable'
    # Print only fixed labels and numeric scores from the published projection.
    if not isinstance(result, dict) or result.get('case_id') != case_id:
        return 'report unavailable'
    if result.get('test_status') == 'error':
        return 'framework error'
    if result.get('privacy') != 'metrics_only':
        return 'report collected'
    benchmark = result.get('benchmark')
    if not isinstance(benchmark, dict):
        return 'report unavailable'
    if benchmark.get('state') == 'out_of_scope':
        return 'out of scope'
    if benchmark.get('state') == 'blocked':
        return 'blocked'
    task = result.get('task')
    state = task.get('state') if isinstance(task, dict) else None
    if not isinstance(state, str):
        return 'outcome unavailable'
    outcome = TASK_OUTCOMES.get(state, 'outcome unavailable')
    score = result.get('quality_score')
    if type(score) in (int, float) and 0 <= score <= 100:
        outcome += f', score {score:g}/100'
    return outcome


def print_progress(queue, active, finished, total, now):
    done = sum(len(history) for history in finished.values())
    lines = [f'Progress | finished {done}/{total} | running {len(active)} | queued {len(queue)}']
    for slot, history in finished.items():
        case = active.get(slot)
        running = f'{case.case_id} ({int(now - case.started)}s)' if case else 'idle'
        completed = ', '.join(history) or '—'
        lines.append(f'Slot {slot:02d} | running: {running} | finished: {completed}')
    print('\n'.join(lines), flush=True)


def run_parallel(ids, workspace, directory, public_root, env, parallel):
    suite = load_suite(workspace)
    if (type(parallel) is not int or not 1 <= parallel <= 10 or not ids or
            len(ids) != len(set(ids)) or set(ids) - set(suite['cases'])):
        raise ValueError('invalid_parallel_case_plan')
    directory.mkdir(parents=True, exist_ok=False)
    queue, active, rows = list(ids), {}, {}
    finished = {slot: [] for slot in range(1, parallel + 1)}
    next_progress = time.monotonic()
    child_env = {**env, 'GITHUB_STEP_SUMMARY': '', 'PYTHONPATH': str(ROOT)}
    try:
        while queue or active:
            while queue and len(active) < parallel:
                slot = next(slot for slot in finished if slot not in active)
                case_id = queue.pop(0)
                work = directory / ('worker-' + case_id)
                output = (directory / (case_id + '.log')).open('wb')
                command = [sys.executable, '-m', 'journey.batch', '--cases', json.dumps([case_id]),
                           '--workspace', workspace, '--work', str(work), '--out', str(public_root)]
                process = subprocess.Popen(command, cwd=ROOT, env=child_env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                started = time.monotonic()
                deadline = started + 60 * (step_timeout_minutes(load_case(case_id)) + CASE_OVERHEAD_MINUTES + 6)
                active[slot] = RunningCase(case_id, process, work, output, started, deadline)
                print(f'Slot {slot:02d} | started {case_id}', flush=True)
            now = time.monotonic()
            if now >= next_progress:
                print_progress(queue, active, finished, len(ids), now)
                next_progress = now + PROGRESS_INTERVAL_SECONDS
            for slot, case in list(active.items()):
                case_id, process = case.case_id, case.process
                expired = time.monotonic() > case.deadline and process.poll() is None
                if expired:
                    stop_stage(process)
                if process.poll() is None:
                    continue
                case.output.close()
                try:
                    result = json.loads((case.work / 'batch-result.json').read_text())['cases']
                    if len(result) != 1 or result[0]['case_id'] != case_id:
                        raise ValueError('worker_result_identity_mismatch')
                    row = result[0]
                    if process.returncode and not row['failed_stages']:
                        row['failed_stages'] = ['worker']
                except (OSError, ValueError, KeyError, TypeError):
                    row = {'case_id': case_id, 'workspace': workspace, 'directory': str(case.work / case_id),
                           'failed_stages': ['worker_timeout' if expired else 'worker_result_missing']}
                rows[case_id] = row
                del active[slot]
                outcome = case_outcome(public_root, case_id, row['failed_stages'])
                finished[slot].append(f'{case_id} [{outcome}]')
                elapsed = int(time.monotonic() - case.started)
                print(f'Slot {slot:02d} | finished {case_id} ({elapsed}s) | {outcome}', flush=True)
                write_summary([rows[key] for key in ids if key in rows], directory,
                              {**env, 'GITHUB_STEP_SUMMARY': ''})
            if active:
                time.sleep(.2)
        print_progress(queue, active, finished, len(ids), time.monotonic())
    finally:
        for case in active.values():
            stop_stage(case.process)
            case.output.close()
        write_summary([rows[key] for key in ids if key in rows], directory, env)
    return int(any(row['failed_stages'] for row in rows.values()) or len(rows) != len(ids))
