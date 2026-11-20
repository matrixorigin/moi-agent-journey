"""Project only the diagnostic fields used by the Journey-3 report."""
import json

from .benchmark_metrics import history_observations
from .failure_categories import LABELS, error_category


DETAIL_KINDS = ('errors', 'timeouts', 'tools', 'scores')
MAX_DETAIL_CHARS = 16_384


def bounded_text(text):
    if len(text) > MAX_DETAIL_CHARS:
        return text[:MAX_DETAIL_CHARS] + ' …（已截断，见 Trace / Task）'
    return text


def evidence_text(value):
    if value is None or value == '':
        return ''
    return bounded_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))


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

    last_task = tasks[-1] if tasks else {}
    pipeline_errors = [{'stage': name, 'reason': 'pipeline_stage_failed', **value}
        for name, value in report.get('pipeline', {}).get('stages', {}).items()
        if name in {'history', 'publish'} and (value.get('exit_code') != 0 or value.get('timed_out'))]
    if 'report_details' in report:
        details = {name: list(report['report_details'][name]) for name in DETAIL_KINDS}
        if pipeline_errors:
            message = case_errors_text(pipeline_errors)
            if details['errors']:
                *keys, earlier = details['errors'][0]
                details['errors'][0] = [*keys, earlier + '\n\n' + message]
            else:
                details['errors'].append([*ids(last_task), message])
        return details

    details = {name: [] for name in DETAIL_KINDS}
    observations = report.get('history_observations') or history_observations(report, row)
    outcome = observations['qa_outcome']
    if outcome == 'error' or report.get('harness_errors') or pipeline_errors:
        failures = [error for error in report.get('failures', []) + report.get('harness_errors', [])
                    if error.get('reason') not in {'case_timeout', 'deadline_exceeded'}]
        message = case_errors_text(failures + pipeline_errors)
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
            evidence_text(command or args), errors_text(call['errors'], ('kind', 'code', 'error_kind', 'status'))])
    if outcome == 'completed' and row['quality_score'] is not None and row['quality_score'] < 50:
        for answer in report.get('rag', {}).get('answers', []):
            reasons = [evidence_text(criterion.get('reason') or criterion['id'])
                       for criterion in answer.get('verdict', {}).get('criteria', [])
                       if criterion.get('passed') is False]
            if reasons:
                details['scores'].append([*ids(answer), row['quality_score'], reasons])
        if not details['scores']:
            details['scores'].append([*ids(last_task), row['quality_score'], None])
    return details


def case_errors_text(errors):
    return errors_text([{**error, 'category': LABELS[error_category(error)]} for error in errors],
                       ('category', 'stage', 'reason', 'http_status', 'exit_code'))


def retained_report(result, row, observations):
    def project(records, fields):
        return [{key: record[key] for key in fields if key in record} for record in records]

    return {
        'case_id': row['case_id'],
        'workspace': 'journey-3',
        'history_observations': observations,
        'report_details': details_for_case(row, {**result, 'history_observations': observations}),
        'tasks': project(result.get('tasks', []), ('task_id', 'conversation_id', 'trace_id', 'phase', 'state')),
        'failed_tool_calls': project(
            [call for call in result.get('tools', {}).get('errors', []) if call.get('outcome') == 'failed'],
            ('task_id', 'call_id', 'seqs')),
        'admission_failures': project(result.get('admission_failures', []),
                                      ('conversation_id', 'reported_task_id', 'confirmed_no_new_task')),
        'model_evaluations': project(result.get('model_evaluations', []), ('label', 'id', 'model', 'state')),
        'stream_interruptions': project(result.get('stream_interruptions', []),
                                        ('task_id', 'reason', 'at', 'reconciled_state', 'reconciled_at')),
        'request_traces': [
            {key: error[key] for key in ('stage', 'task_id', 'conversation_id', 'trace_headers') if key in error}
            for error in result.get('failures', []) + result.get('harness_errors', []) if error.get('trace_headers')
        ],
    }
