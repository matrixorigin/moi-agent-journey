"""Attribute observed failures without changing task, delivery or quality verdicts."""
from collections import Counter


LABELS = {
    'qa_service': 'QA 服务',
    'qa_runtime': 'QA 智能体运行时',
    'transport': '对话传输',
    'evaluation_service': '评分服务',
    'evaluator': '验收器',
    'pipeline': '取证发布流程',
    'harness': '测试框架',
}
STAGES = {'prepare', 'execute', 'report', 'history', 'publish', 'finalize_history', 'worker',
          'worker_timeout', 'worker_result_missing', 'worker_cancelled', 'orchestration'}
RUNTIME_FAILURES = {'agent_runtime_stream_error', 'agent_runtime_admission_error',
                    'agent_failed', 'agent_rejected', 'agent_auth-required', 'agent_input-required'}
ADMISSION_REASONS = {'AGENT_A2A_ADMISSION_TIMEOUT'}


def error_category(error):
    reason = str(error.get('reason', '')).split(':', 1)[0]
    stage = str(error.get('stage', ''))
    details = error.get('details')
    data = details.get('data') if isinstance(details, dict) else None
    runtime = reason in RUNTIME_FAILURES or (reason == 'rpc_error' and isinstance(data, dict)
                                            and data.get('reason') in ADMISSION_REASONS)
    remote = (reason == 'http_error' or reason == 'transport_error' or
              reason == 'rpc_error')
    if stage in {'semantic_judge', 'knowledge_evidence_validation'}:
        return 'evaluation_service' if remote or runtime else 'evaluator'
    if runtime:
        return 'qa_runtime'
    if remote:
        return 'qa_service'
    if reason in {'stream_closed_before_terminal', 'incomplete_stream_frame',
                  'stream_ended_without_task', 'invalid_stream_response',
                  'send_outcome_unknown', 'active_task_not_archived'}:
        return 'transport'
    if stage in {'history', 'publish'}:
        return 'pipeline'
    return 'harness'


def failure_counts(result):
    """Only fixed category names and integer counts may cross J3's privacy boundary."""
    if result.get('privacy') == 'metrics_only' and 'failure_categories' in result:
        counts = result['failure_categories']
        if (not isinstance(counts, dict) or set(counts) - set(LABELS) or
                any(type(value) is not int or value < 0 for value in counts.values())):
            raise ValueError('invalid_failure_categories')
        return dict(counts)
    errors = list(result.get('harness_errors', []))
    # A subprocess exit is a consequence of its recorded error, not a second
    # independent framework defect. Preserve the original record in result.json.
    specific = any(not str(error.get('stage', '')).startswith('batch:') for error in errors)
    counts = Counter(error_category(error) for error in errors if not (
        specific and error.get('stage') == 'batch:execute' and error.get('reason') == 'process_failed'))
    for error in result.get('failures', []):
        if error.get('reason') in RUNTIME_FAILURES:
            counts['qa_runtime'] += 1
    for name, stage in result.get('pipeline', {}).get('stages', {}).items():
        if (name in {'history', 'publish'} and (stage.get('exit_code') != 0 or stage.get('timed_out'))
                and not any(error.get('stage') == name for error in errors)):
            counts['pipeline'] += 1
    return dict(counts)


def failed_stage_names(stages):
    """Never print arbitrary worker strings or private exception text to Actions."""
    return [name if name in STAGES else 'unknown' for name in stages]


def failure_label(counts):
    for key, label in (('harness', 'framework error'), ('pipeline', 'pipeline error'),
                       ('evaluation_service', 'evaluation service error'), ('evaluator', 'evaluator error'),
                       ('qa_service', 'QA service error'), ('qa_runtime', 'agent runtime error'),
                       ('transport', 'transport error')):
        if counts.get(key):
            return label
    return None
