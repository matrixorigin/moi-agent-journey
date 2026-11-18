"""Bounded case processes: each has its own signals, deadlines and work files."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .batch import stop_stage, write_summary, CASE_OVERHEAD_MINUTES
from .catalog import ROOT, load_case, load_suite, step_timeout_minutes


def run_parallel(ids, workspace, directory, public_root, env, parallel):
    suite = load_suite(workspace)
    if (type(parallel) is not int or not 1 <= parallel <= 10 or not ids or
            len(ids) != len(set(ids)) or set(ids) - set(suite['cases'])):
        raise ValueError('invalid_parallel_case_plan')
    directory.mkdir(parents=True, exist_ok=False)
    queue, active, rows = list(ids), {}, {}
    child_env = {**env, 'GITHUB_STEP_SUMMARY': '', 'PYTHONPATH': str(ROOT)}
    try:
        while queue or active:
            while queue and len(active) < parallel:
                case_id = queue.pop(0)
                work = directory / ('worker-' + case_id)
                output = (directory / (case_id + '.log')).open('wb')
                command = [sys.executable, '-m', 'journey.batch', '--cases', json.dumps([case_id]),
                           '--workspace', workspace, '--work', str(work), '--out', str(public_root)]
                process = subprocess.Popen(command, cwd=ROOT, env=child_env, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 60 * (step_timeout_minutes(load_case(case_id)) + CASE_OVERHEAD_MINUTES + 6)
                active[case_id] = (process, work, output, deadline)
                print(f'{case_id}: started', flush=True)
            for case_id, (process, work, output, deadline) in list(active.items()):
                expired = time.monotonic() > deadline and process.poll() is None
                if expired:
                    stop_stage(process)
                if process.poll() is None:
                    continue
                output.close()
                try:
                    result = json.loads((work / 'batch-result.json').read_text())['cases']
                    if len(result) != 1 or result[0]['case_id'] != case_id:
                        raise ValueError('worker_result_identity_mismatch')
                    row = result[0]
                    if process.returncode and not row['failed_stages']:
                        row['failed_stages'] = ['worker']
                except (OSError, ValueError, KeyError, TypeError):
                    row = {'case_id': case_id, 'workspace': workspace, 'directory': str(work / case_id),
                           'failed_stages': ['worker_timeout' if expired else 'worker_result_missing']}
                rows[case_id] = row
                del active[case_id]
                print(f'{case_id}: collected; harness_errors={len(row["failed_stages"])}', flush=True)
                write_summary([rows[key] for key in ids if key in rows], directory,
                              {**env, 'GITHUB_STEP_SUMMARY': ''})
            if active:
                time.sleep(.2)
    finally:
        for process, _, output, _ in active.values():
            stop_stage(process)
            output.close()
        write_summary([rows[key] for key in ids if key in rows], directory, env)
    return int(any(row['failed_stages'] for row in rows.values()) or len(rows) != len(ids))
