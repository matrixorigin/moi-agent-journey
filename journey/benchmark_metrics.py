"""Stable history columns and an allowlist for public business-case metrics."""
import json
import os
from pathlib import Path

from .evidence import utc_now
from .failure_categories import failure_counts


LEGACY_TIMING_FIELDS = {'completed_seconds', 'answer_timing'}
EVALUATOR_VERSION = 'correctness-facts-v2'


def model_rounds(path, tasks):
    ids = {task['task_id'] for task in tasks if not task.get('phase', '').startswith('judge:')}
    rounds, seen, active = set(), set(), {}
    if not Path(path).exists():
        return None
    for line in Path(path).read_text().splitlines():
        event = json.loads(line)
        payload = event.get('payload', {})
        event_key=(event.get('task_id'),event.get('seq'))
        if event_key in seen:continue
        seen.add(event_key)
        if (event.get('task_id') in ids and event.get('kind') == 'lifecycle' and
                payload.get('type') in {'llm_started', 'llm_finished'}):
            identity = payload.get('call_id')
            if identity is None:
                identity = payload.get('turn')
            task=event['task_id']
            if payload['type']=='llm_started':
                key=(task,str(identity)) if identity is not None else (task,'start:'+str(event['seq']))
                rounds.add(key);active[task]=key
            elif identity is not None:
                previous=active.pop(task,None)
                if previous:rounds.discard(previous)
                rounds.add((task,str(identity)))
            elif active.pop(task, None) is None:
                rounds.add((task, 'finish:' + str(event['seq'])))
    return len(rounds) if rounds else None


def quality_score(result):
    answers = [answer for answer in result.get('rag', {}).get('answers', [])
               if answer.get('evaluation_state') != 'context_only']
    values = [answer.get('metrics', {}).get('correctness', {}) for answer in answers]
    if not values or any(not item.get('total') or item.get('unavailable') for item in values):
        return None
    return round(100 * sum(item.get('passed', 0) for item in values) / sum(item['total'] for item in values), 2)


def delivered_answers(result):
    return [answer for answer in result.get('rag', {}).get('answers', [])
            if answer.get('evaluation_state') != 'context_only' and
            isinstance(answer.get('text'), str) and answer['text'].strip()]


def delivered_answer_seconds(answers):
    durations = [answer.get('seconds') for answer in answers]
    if durations and all(type(value) in (int, float) for value in durations):
        return round(sum(durations), 3)
    return None


def history_observations(result, row, events=None):
    answers = delivered_answers(result)
    state = row['task_state']
    metrics = result.get('tools', {}).get('metrics') or {}
    rounds = None
    if row['benchmark_state'] != 'eligible':
        outcome = 'excluded'
    elif answers:
        outcome = 'completed'
        if state == 'completed':
            rounds = row['model_round_count']
        elif events is not None:
            rounds = model_rounds(events, answers)
    elif state == 'timeout':
        outcome = 'timeout'
    elif result.get('tasks') or result.get('agent_attempted') or result.get('admission_failures'):
        outcome = 'error'
    else:
        outcome = 'excluded'
    return {'qa_outcome': outcome, 'completed_model_rounds': rounds,
            'tool_succeeded': metrics.get('succeeded'), 'tool_failed': metrics.get('failed')}


def history_record(result, case, events):
    from .benchmark_store import canonical
    tasks = [task for task in result.get('tasks', []) if not task.get('phase', '').startswith('judge:')]
    questions = [turn['prompt'] for session in case.get('sessions', []) for turn in session['turns']
                 if 'rag_reference' in turn]
    # Each new ops case is one scored question. Existing multi-turn cases retain
    # the complete question sequence in a single case-level history record.
    state = result.get('task', {}).get('state', 'unknown')
    benchmark_state = result.get('benchmark', {}).get('state', 'eligible')
    duration = result.get('task_seconds') if tasks or result.get('agent_attempted') else None
    if state != 'completed' and benchmark_state == 'eligible':
        answers = delivered_answers(result)
        if answers:
            duration = delivered_answer_seconds(answers)
    failures = result.get('failures', []) + result.get('harness_errors', [])
    admissions = result.get('admission_failures', [])
    def identities(field):
        values = [task.get(field) for task in tasks]
        if field == 'trace_id':
            values += [next((value for key, value in item.get('trace_headers', {}).items()
                              if key.lower() == 'x-trace-id'), None) for item in admissions]
        else:
            values += [item.get('conversation_id') for item in admissions]
        values = list(dict.fromkeys(value for value in values if value))
        return values[0] if len(values) == 1 else canonical(values) if values else None
    return {'case_id': case['id'], 'question': case.get('original_question') or '\n\n'.join(questions),
        'agent_name': result.get('benchmark_agent_name') or case.get('agent_name') or
                      result.get('rag', {}).get('target', {}).get('agent_id', ''),
        'duration_seconds': duration,
        'quality_score': quality_score(result),
        'evaluated_at': (result.get('started_at') or utc_now()).replace('T', ' ').replace('Z', '').split('+')[0],
        'tool_call_count': (result.get('tools', {}).get('metrics') or {}).get('calls'),
        'model_round_count': model_rounds(events, tasks),
        'failure_reason': '' if state == 'completed' and not failures else canonical([
            {key: value[:512] if isinstance(value, str) else value for key, value in failure.items()
             if key in {'stage', 'reason', 'http_status'} and isinstance(value, (str, int))}
            for failure in failures or [{'reason': state}]]),
        'trace_id': identities('trace_id'), 'session_id': identities('conversation_id'),
        **history_identity(result, case),
        'benchmark_state': benchmark_state,
        'snapshot_id': case.get('snapshot_id', 'existing-document-corpus'),
        'task_state': state, 'evaluation_state': canonical([answer.get('evaluation_state')
            for answer in result.get('rag', {}).get('answers', [])]),
        'harness_state': result.get('test_status', 'error'), 'model': case['model'],
        'qa_version': canonical(result.get('qa_version_start', {})),
        'workflow_commit': os.environ.get('GITHUB_SHA', ''), 'evaluator_version': EVALUATOR_VERSION}


def history_identity(result, case):
    from .benchmark_store import digest
    return {'run_id': str(os.environ.get('GITHUB_RUN_ID') or result.get('run_id')),
            'run_attempt': int(os.environ.get('GITHUB_RUN_ATTEMPT', '1')),
            'case_revision': case.get('private_spec', {}).get('revision') or digest(case)}


def public_result(result):
    """Build from known scalar metrics; never copy arbitrary nested records."""
    import math
    from datetime import datetime
    def number(value):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError('invalid_public_numeric_metric')
        return value
    benchmark = result.get('benchmark', {}).get('state', 'eligible')
    if benchmark not in {'eligible', 'blocked', 'out_of_scope'}:
        raise ValueError('invalid_public_benchmark_state')
    started_at = result.get('started_at')
    if started_at is not None:
        started_at = datetime.fromisoformat(started_at).isoformat()
    def counts(name, fields):
        return {key: result.get(name, {}).get(key) for key in fields}
    rag = result.get('rag', {})
    answers = []
    for index,answer in enumerate(rag.get('answers', []),1):
        metrics = {name: {key: value.get(key) for key in
                    ('passed', 'valid', 'total', 'rate', 'expected_total', 'unavailable') if key in value}
                   for name, value in answer.get('metrics', {}).items()
                   if name in {'correctness', 'faithfulness', 'citation_support', 'citation_integrity'}}
        answers.append({'session': 'qa', 'phase': 'question', 'question': '[Stored in QA]', 'text': '',
            'task_id': result['case_id']+'-answer-'+str(index), 'conversation_id': None,
            'trace_id': None, 'seconds': number(answer.get('seconds')),
            'evaluation_state': answer['evaluation_state'], 'metrics': metrics, 'verdict': {},
            'efficiency': {key: number(answer.get('efficiency', {}).get(key)) for key in ('tool_calls', 'tool_rounds')}})
    tools = result.get('tools', {})
    raw = (tools.get('metrics') or {}) if tools.get('state')=='available' else {}
    metrics = {key: raw.get(key, 0) for key in ('calls', 'succeeded', 'failed', 'skipped', 'unknown',
               'tool_rounds', 'round_unmapped_calls', 'unidentified_events', 'success_rate')}
    # Tool names/parameters/error strings may include tenant or business names.
    metrics['by_tool'] = [{'tool': 'private-tools', **{key: metrics[key] for key in
        ('calls','succeeded','failed','skipped','unknown','success_rate')}}] if metrics['calls'] else []
    quality = result.get('quality', {})
    failed_count = len(quality.get('failed_checks', []))
    return {'case_id': result['case_id'], 'workspace': 'journey-3', 'title': result['case_id'] + ' · Knowledge Base Q&A',
        'privacy': 'metrics_only', 'test_status': result['test_status'],
        'failure_categories': failure_counts(result),
        'benchmark': {'state': benchmark},
        'task': counts('task', ['state']), 'agent': counts('agent', ['state','completed_turns','expected_turns']),
        'delivery': counts('delivery', ['state','completed_sessions','expected_sessions']),
        'quality': {'state': quality['state'], 'passed_checks': number(quality.get('passed_checks')),
            'total_checks': number(quality.get('total_checks')), 'failed_checks': [{'rule': 'private_criterion'}] * failed_count},
        'tools': {'state': tools['state'], 'complete': tools.get('complete') is True, 'metrics': metrics,
                  'errors': [], 'judge_errors': [], 'runtime_sessions': {}},
        'tasks': [],
        'conversations': [], 'failures': [{'stage': 'agent', 'reason': 'see_private_QA_report'}]
            if result.get('failures') else [],
        'harness_errors': [{'stage': 'harness', 'reason': 'see_private_QA_report'}]
            if result.get('harness_errors') else [],
        'task_seconds': number(result.get('task_seconds')), 'total_seconds': number(result.get('total_seconds')),
        'started_at': started_at, 'quality_score': quality_score(result),
        'rag': {'corpus': 'knowledge-base', 'expected_answers': rag.get('expected_answers', 1), 'answers': answers}}
