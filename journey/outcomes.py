"""Separate agent execution, delivery and quality without interpreting a red check as a root cause."""
from collections import Counter

from .catalog import case_executor


def assess(case, result, raw_status):
    sessions = case.get('sessions', [])
    expected_phases = [(session['id'] + ':' + turn['phase'], turn.get('expected_state', 'completed'))
                       for session in sessions for turn in session['turns']]
    tasks = [task for task in result.get('tasks', [])
             if not str(task.get('phase', '')).startswith('judge:')]
    completed = [(task.get('phase'), task.get('state')) for task in tasks]
    matched = sum((Counter(expected_phases) & Counter(completed)).values())
    reasons = [item.get('reason', '') for item in result.get('failures', [])]
    agent_failure = (any(reason in {'agent_failed', 'agent_canceled', 'agent_rejected',
                                    'agent_input-required', 'agent_auth-required',
                                    'agent_runtime_stream_error'} for reason in reasons) or
                     any(task.get('state') in {'failed', 'rejected'} for task in tasks))
    if expected_phases and matched == len(expected_phases):
        agent_state = 'canceled' if all(state == 'canceled' for _, state in expected_phases) else 'completed'
    elif raw_status == 'timeout' and tasks:
        agent_state = 'timeout'
    elif agent_failure:
        agent_state = 'abnormal'
    elif result.get('harness_errors') or not tasks:
        agent_state = 'unknown'
    elif case_executor(case) in {'browser', 'api'} and all(task.get('state') == 'completed' for task in tasks):
        # A functional prerequisite can prevent later turns without an agent failure.
        agent_state = 'unknown'
    else:
        agent_state = 'abnormal'
    agent = {'state': agent_state, 'completed_turns': matched, 'expected_turns': len(expected_phases),
             'tasks': [{'phase': task.get('phase'), 'state': task.get('state'),
                        'task_id': task.get('task_id')} for task in tasks]}

    delivered_sessions = {item.get('id'): item for item in result.get('sessions', [])}
    delivery_sessions = []
    for session in sessions:
        actual = delivered_sessions.get(session['id'])
        expected = session.get('expected_files', [])
        files = actual.get('outputs', {}) if isinstance(actual, dict) else {}
        if session.get('delivery_kind') == 'agent':
            resource = actual.get('created_agent') if isinstance(actual, dict) else None
            delivered = isinstance(resource, dict) and resource.get('verified') is True
            delivery_sessions.append({'session': session['id'], 'delivery_kind': 'agent', 'delivered': delivered,
                                      'expected_files': [], 'missing_files': []})
        elif session.get('delivery_kind') == 'answer':
            turns = session.get('turns', [])
            final_phase = session['id'] + ':' + turns[-1]['phase'] if turns else None
            final_tasks = [task for task in tasks if task.get('phase') == final_phase]
            delivered = (isinstance(actual, dict) and actual.get('answer_delivered') is True and
                         len(final_tasks) == 1 and isinstance(final_tasks[0].get('task_id'), str) and
                         bool(final_tasks[0]['task_id']) and actual.get('final_task_id') == final_tasks[0]['task_id'])
            delivery_sessions.append({'session': session['id'], 'delivery_kind': 'answer', 'delivered': delivered,
                                      'expected_files': [], 'missing_files': []})
        elif session.get('delivery_kind') == 'candidate':
            delivered = isinstance(actual, dict) and actual.get('candidate_verified') is True
            delivery_sessions.append({'session': session['id'], 'delivery_kind': 'candidate', 'delivered': delivered,
                                      'expected_files': [], 'missing_files': []})
        elif session.get('delivery_kind') == 'file':
            delivered = isinstance(actual, dict) and actual.get('artifact_delivered') is True
            delivery_sessions.append({'session': session['id'], 'delivery_kind': 'file', 'delivered': delivered,
                                      'expected_files': [], 'missing_files': []})
        elif session.get('delivery_kind') == 'cancellation':
            delivered = isinstance(actual, dict) and actual.get('cancellation_confirmed') is True
            delivery_sessions.append({'session': session['id'], 'delivery_kind': 'cancellation', 'delivered': delivered,
                                      'expected_files': [], 'missing_files': []})
        else:
            delivery_sessions.append({'session': session['id'], 'delivered': actual is not None,
                                      'expected_files': expected,
                                      'missing_files': [name for name in expected if name not in files]})
    complete = sum(item['delivered'] and not item['missing_files'] for item in delivery_sessions)
    if delivery_sessions and complete == len(delivery_sessions) and all(
            item.get('delivery_kind') == 'cancellation' for item in delivery_sessions):
        delivery_state = 'canceled'
    elif delivery_sessions and complete == len(delivery_sessions):
        delivery_state = 'delivered'
    elif complete:
        delivery_state = 'partial'
    elif any(reason.startswith('final_output_bundle_not_unique') for reason in reasons):
        details = next((item.get('details') for item in result.get('failures', [])
                        if item.get('reason') == 'final_output_bundle_not_unique'), None)
        count = details.get('candidate_count') if isinstance(details, dict) else None
        delivery_state = 'not_delivered' if count == 0 else 'ambiguous'
    elif any(reason.startswith(('missing_deliverables', 'invalid_output_archive')) for reason in reasons):
        delivery_state = 'incomplete'
    elif result.get('harness_errors') or not tasks:
        delivery_state = 'unknown'
    else:
        delivery_state = 'not_delivered'
    delivery = {'state': delivery_state, 'completed_sessions': complete,
                'expected_sessions': len(delivery_sessions), 'sessions': delivery_sessions}

    if agent_state in {'abnormal', 'timeout', 'unknown'}:
        task_state = agent_state
    elif agent_state == 'canceled' and delivery_state == 'canceled':
        task_state = 'canceled'
    elif delivery_state == 'delivered':
        task_state = 'completed'
    elif delivery_state in {'not_delivered', 'partial', 'incomplete'}:
        task_state = 'not_delivered'
    else:
        task_state = 'unknown'

    validation = result.get('validation', {})
    checks = validation.get('checks', [])
    validator_error = any(item.get('stage') in {'validation', 'semantic_judge'}
                          for item in result.get('harness_errors', []))
    expected_answer_checks = sum(session.get('expected_checks', sum(1 + len(turn.get('assertions', []))
                                      for turn in session['turns'])) for session in sessions
                                 if session.get('delivery_kind') in {'agent', 'answer', 'candidate', 'file', 'cancellation'})
    incomplete_answer_checks = bool(expected_answer_checks) and len(checks) < expected_answer_checks
    if checks:
        failed = [item for item in checks if not item.get('passed')]
        quality = {'state': 'partial' if validator_error or incomplete_answer_checks else 'checked',
                   'reason': 'unfinished_turns' if incomplete_answer_checks else 'validator_error' if validator_error else None,
                   'passed_checks': len(checks) - len(failed),
                   'total_checks': len(checks), 'failed_checks': failed}
    else:
        reason = ('validator_error' if validator_error else
                  'not_evaluated' if delivery_state == 'delivered' else 'no_validated_delivery')
        quality = {'state': 'unavailable', 'passed_checks': None, 'total_checks': None,
                   'failed_checks': [], 'reason': reason}
    return {'state': task_state}, agent, delivery, quality
