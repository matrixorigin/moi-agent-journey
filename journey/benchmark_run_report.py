"""Generate the private Journey-3 dashboard from a single saved run attempt."""
import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from .benchmark_history import RUN_ID, render_history
from .benchmark_metrics import history_observations
from .benchmark_store import HISTORY, configured_store, literal
from .evidence import Evidence, configured_secrets


def evidence_text(value):
    if value is None or value == '':
        return ''
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def errors_text(errors, labels):
    return '\n\n'.join(dict.fromkeys(
        '\n'.join(part for part in (
            ' · '.join(evidence_text(error.get(key)) for key in labels if error.get(key) is not None),
            evidence_text(error.get('detail', error.get('details'))),
        ) if part)
        for error in errors
    )).strip()


def details_for_case(row, report):
    case = row['case_id']
    tasks = [task for task in report.get('tasks', []) if not task.get('phase', '').startswith('judge:')]
    by_task = {task['task_id']: task for task in tasks}

    def ids(task):
        return [case, task.get('conversation_id') or row['session_id'], task.get('trace_id') or row['trace_id']]

    details = {'errors': [], 'timeouts': [], 'tools': [], 'scores': []}
    outcome = history_observations(report, row)['qa_outcome']
    last_task = tasks[-1] if tasks else {}
    if outcome == 'error':
        failures = [error for error in report.get('failures', []) + report.get('harness_errors', [])
                    if error.get('reason') not in {'case_timeout', 'deadline_exceeded'}]
        message = errors_text(failures, ('stage', 'reason', 'http_status', 'exit_code'))
        runtime_error = last_task.get('runtime_error')
        if runtime_error:
            message = '\n\n'.join(filter(None, (message, evidence_text(runtime_error))))
        if message:
            details['errors'].append([*ids(last_task), message])
    if outcome == 'timeout':
        details['timeouts'].append([*ids(last_task), row['duration_seconds']])
    for call in report.get('tools', {}).get('errors', []):
        if call.get('outcome') != 'failed':
            continue
        tool = call.get('tool') or '未知工具'
        args = call.get('arguments')
        command = args.get('command') if isinstance(args, dict) else None
        details['tools'].append([*ids(by_task.get(call.get('task_id'), {})), tool,
            command or evidence_text(args), errors_text(call['errors'], ('kind', 'code', 'error_kind', 'status'))])
    if outcome == 'completed' and row['quality_score'] is not None and row['quality_score'] < 50:
        for answer in report.get('rag', {}).get('answers', []):
            reasons = [criterion.get('reason') or criterion['id'] for criterion in answer.get('verdict', {}).get('criteria', [])
                       if criterion.get('passed') is False]
            if reasons:
                details['scores'].append([*ids(answer), row['quality_score'], reasons])
        if not details['scores']:
            details['scores'].append([*ids(last_task), row['quality_score'], None])
    return details


def run_details(store, run, attempt):
    if not RUN_ID.fullmatch(run) or type(attempt) is not int or attempt < 1:
        raise ValueError('invalid_report_run')
    fields = ['case_id', 'task_state', 'benchmark_state', 'duration_seconds', 'model_round_count',
              'quality_score', 'session_id', 'trace_id']
    details = {'run_id': run, 'attempt': attempt, 'errors': [], 'timeouts': [], 'tools': [], 'scores': []}
    page_size, cursor = 16, ''
    while True:
        result = store.sql('SELECT '+','.join(fields)+',report_json FROM '+HISTORY+
                           ' WHERE run_id='+literal(run)+' AND run_attempt='+literal(attempt)+
                           ' AND case_id>'+literal(cursor)+' ORDER BY case_id LIMIT '+str(page_size), page_size)
        page = result['rows']
        if result.get('truncated'):
            raise ValueError('truncated_private_report_page')
        for *values, raw in page:
            if len(values) != len(fields):
                raise ValueError('invalid_private_report_row')
            row = dict(zip(fields, values))
            if not re.fullmatch(r'J3-\d{2,}', row['case_id'] or '') or row['case_id'] <= cursor:
                raise ValueError('invalid_private_report_case_order')
            cursor = row['case_id']
            for field in ('quality_score', 'duration_seconds'):
                row[field] = None if row[field] is None else float(row[field])
            for kind, records in details_for_case(row, json.loads(raw)).items():
                details[kind].extend(records)
        if len(page) < page_size:
            return details


def publish(store, rows, run, attempt):
    details = run_details(store, run, attempt)
    with tempfile.TemporaryDirectory() as tmp:
        details = Evidence(tmp, configured_secrets()).clean(details)
    filename = f'journey-3-report-{run}-attempt-{attempt}.html'
    store.upload_private(filename, render_history(rows, details).encode('utf-8'), 'text/html; charset=utf-8')
    return filename


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--history', required=True, type=Path)
    args = parser.parse_args()
    try:
        rows = json.loads(args.history.read_text())
        filename = publish(configured_store(), rows, os.environ['GITHUB_RUN_ID'], int(os.environ['GITHUB_RUN_ATTEMPT']))
        print('Journey-3 private dashboard saved in the QA Catalog')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                stream.write(f'\nJourney-3 完整报告已保存到 QA 工作区 Catalog：`{filename}`。下载后用浏览器打开。\n')
    except Exception:
        print('::error::Journey-3 could not save the private dashboard to the QA Catalog')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
