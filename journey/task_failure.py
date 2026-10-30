"""Induce a real task failure using a request-local nonexistent model."""
from .builder_flow import ProductCaseFailure
from .client import Failure


def expected_model_failure(execution, cid, phase, prompt, session, checks):
    model = 'journey-missing-' + execution.report['run_id']
    before = len(execution.tasks)
    observed = None
    try:
        execution.send(cid, phase, prompt, model=model)
    except Failure as error:
        if error.code != 'agent_failed':
            raise
        observed = error
    added = execution.tasks[before:]
    entry = added[0] if len(added) == 1 else {}
    error = entry.get('runtime_error') or {}
    passed = (observed is not None and entry.get('state') == 'failed' and
              entry.get('conversation_id') == cid and entry.get('phase') == phase and
              bool(entry.get('trace_id')) and error.get('provider') == 'astra' and
              error.get('error_code') == 'server_error')
    checks.append({'session': session, 'rule': '不存在的请求模型触发真实失败任务并保留 trace',
                   'passed': passed, 'detail': '' if passed else 'expected_model_task_failure_not_observed'})
    execution.report.setdefault('expected_failures', []).append({
        'phase': phase, 'model': model, 'task_id': entry.get('task_id'), 'observed': passed})
    execution.report['validation'] = {'checks': checks}
    execution.checkpoint()
    if not passed:
        raise ProductCaseFailure('expected_model_task_failure_not_observed')
    return entry
