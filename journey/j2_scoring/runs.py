"""Read frozen Journey-2 results and summarize equal-weight case scores."""
import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from journey.catalog import ROOT, load_case
from .registry import resolve_checkpoints, validate_registry
from .score import display, score_functional_result


POLICY = 'j2-checkpoints-equal-v1'
DEFAULT_SUITE = ROOT / 'datasets/journey-2/scoring/agent76-v1.json'
DOMAIN_LABELS = {
    'conversation': '会话与上下文', 'multimodal': '多模态与文件输入',
    'delivery': '成果交付', 'tools': '工具与沙箱', 'feedback': '用户反馈',
    'interaction': '结构化交互', 'agent_lifecycle': '智能体构建与生命周期',
    'skill': 'Skill', 'custom_tool': '自定义 Tool', 'web_search': '网页检索与打开',
}


def load_suite(path):
    path = Path(path).resolve()
    suite = json.loads(path.read_text())
    items = suite.get('cases')
    if (not isinstance(suite.get('suite_id'), str) or not suite['suite_id'] or
            not isinstance(items, list) or not items):
        raise ValueError('invalid_j2_scoring_suite')
    ids = []
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or
                not item['id'].startswith('J2-') or item.get('domain') not in DOMAIN_LABELS or
                item.get('executor', 'functional') not in {'functional', 'persistent'}):
            raise ValueError('j2_scoring_requires_existing_functional_cases')
        case = load_case(item['id'])
        if case['workspace'] != 'journey-2':
            raise ValueError('j2_scoring_requires_journey_2')
        if item.get('executor') == 'persistent' and case.get('persistent_conversation') is None:
            raise ValueError('persistent_contract_missing')
        ids.append(item['id'])
    if len(ids) != len(set(ids)) or set(ids) & set(suite.get('excluded_cases', [])):
        raise ValueError('duplicate_or_excluded_j2_case')
    configured = suite.get('checkpoint_registry')
    registry = None
    if configured:
        registry = validate_registry(json.loads((path.parent / configured).read_text()))
    return {**suite, '_registry': registry, '_path': str(path)}


def load_results(run):
    results = {}
    for path in sorted((Path(run) / 'results').glob('J2-*/result.json')):
        result = json.loads(path.read_text())
        case_id = result.get('case_id')
        if case_id != path.parent.name or case_id in results:
            raise ValueError('result_case_identity_mismatch:' + str(path))
        results[case_id] = {**result, 'evidence_path': str(path.resolve())}
    return results


def missing_case(item):
    return {
        'case_id': item['id'], 'domain': item['domain'], 'title': load_case(item['id'])['title'],
        'execution_status': 'not_run', 'score': None, 'verdict': None,
        'excluded_reason': 'not_run', 'reason': 'not_run', 'scoring_status': 'unscored',
        'normal_completed': False, 'coverage_eligible': False,
        'expected_checks': None, 'actual_checks': 0, 'passed_checks': 0,
        'forced_after_timeout': False, 'assertions_complete': False,
        'checkpoint_registry_sha256': None, 'scoring_policy': POLICY,
        'persistent_mode': None, 'persistent_history_context': None,
        'qa_version_start': None, 'qa_version_end': None, 'harness_errors': [],
        'evidence_path': None, 'task_seconds': None, 'checkpoints': [],
    }


def case_view(result, item, registry=None):
    result = copy.deepcopy(result)
    case = load_case(item['id'])
    case_path = ROOT / 'cases/journey-2' / item['id'] / 'case.json'
    checks = result.get('validation', {}).get('checks', [])
    identity = {}
    identity_error = None
    if isinstance(checks, list) and all(isinstance(c, dict) for c in checks):
        try:
            checks, identity = resolve_checkpoints(registry, item['id'], checks)
            result.setdefault('validation', {})['checks'] = checks
        except (KeyError, TypeError, ValueError):
            identity_error = 'checkpoint_identity_mismatch'
    scored = score_functional_result(result, result['evidence_path'], case, case_path)
    metadata = result.get('always_assert') or {}
    # Multipliers are a separate policy. Never silently turn an old adjusted score into 100.
    foreign_policy = (any(isinstance(c, dict) and c.get('sql_path_scoring') for c in checks)
                      if isinstance(checks, list) else False)
    foreign_policy |= (metadata.get('path_factor') not in (None, '1.00', '1', 1) or
                       'sql-path' in str(metadata.get('scoring_policy', '')))
    if foreign_policy or identity_error:
        scored.update(score=None, verdict=None, scoring_status='unscored',
                      reason='unsupported_scoring_policy' if foreign_policy else identity_error)
    persistent = result.get('persistent_conversation') or {}
    continuity = persistent.get('continuity')
    before, after = result.get('qa_version_start'), result.get('qa_version_end')
    for check in scored['checkpoints']:
        if not check.get('checkpoint_id'):
            key = json.dumps([check['session'], check['rule'], check['index']], ensure_ascii=False)
            check['checkpoint_id'] = item['id'] + '.observed.' + hashlib.sha256(key.encode()).hexdigest()[:16]
            check['checkpoint_id_source'] = 'observed_rule_occurrence'
    return {
        **scored, 'domain': item['domain'], 'title': case['title'],
        'case_definition_sha256': hashlib.sha256(case_path.read_bytes()).hexdigest(),
        'execution_status': scored['original_status'], 'excluded_reason': scored['reason'],
        'normal_completed': (scored['original_status'] in {'passed', 'failed'} and
                             not result.get('harness_errors') and result.get('blocked') is not True and
                             before is not None and before == after),
        'coverage_eligible': scored['score'] is not None,
        'passed_checks': sum(c['status'] == 'passed' for c in scored['checkpoints']),
        'assertions_complete': scored['score'] is not None,
        'forced_after_timeout': metadata.get('forced_after_timeout', False),
        'scoring_policy': POLICY,
        'source_scoring_policy': metadata.get('scoring_policy'),
        'source_score': metadata.get('score'),
        'checkpoint_registry_sha256': identity.get('checkpoint_registry_sha256'),
        'persistent_mode': persistent.get('mode'),
        'persistent_sequence': persistent.get('sequence'),
        'persistent_history_context': ('bootstrap' if continuity == 'bootstrap' else
                                       'checkpoint' if continuity is not None else None),
    }


def environment_summary(items, results, batch, registry=None):
    cases = [case_view(results[item['id']], item, registry) if item['id'] in results else missing_case(item)
             for item in items]
    by_id = {row['case_id']: row for row in batch.get('cases', [])}
    for case in cases:
        row = by_id.get(case['case_id'], {})
        case['selected_attempt'] = row.get('selected_attempt')
        case['attempts'] = row.get('attempts', [])
    domains = []
    for domain in dict.fromkeys(item['domain'] for item in items):
        selected = [case for case in cases if case['domain'] == domain]
        scored = [case for case in selected if case['score'] is not None]
        value = sum((Decimal(c['score']) for c in scored), Decimal(0)) / len(scored) if scored else None
        domains.append({'domain': domain, 'score': display(value), 'scored': len(scored),
                        'total': len(selected), 'coverage': display(Decimal(len(scored)) * 100 / len(selected))})
    scored = [case for case in cases if case['score'] is not None]
    completed = sum(case['normal_completed'] for case in cases)
    versions = sorted({case[key]['version'] for case in cases for key in ('qa_version_start', 'qa_version_end')
                       if isinstance(case.get(key), dict) and case[key].get('version')})
    return {
        'model': batch.get('model'), 'environment': batch.get('environment'),
        'workspace_id': batch.get('workspace_id'), 'versions': versions,
        'scored': len(scored), 'total': len(cases),
        'coverage': display(Decimal(len(scored)) * 100 / len(cases)),
        'normal_completed': completed,
        'normal_completion_coverage': display(Decimal(completed) * 100 / len(cases)),
        'passed': sum(c['verdict'] == 'PASS' for c in cases),
        'partial': sum(c['verdict'] == 'PARTIAL' for c in cases),
        'failed': sum(c['verdict'] == 'FAIL' for c in cases),
        'timeouts': sum(c['execution_status'] == 'timeout' for c in cases),
        'framework_errors': sum(c['excluded_reason'] == 'framework_error' for c in cases),
        'blocked': sum(c['excluded_reason'] == 'blocked' for c in cases),
        'not_run': sum(c['excluded_reason'] == 'not_run' for c in cases),
        'domains': domains, 'cases': cases,
    }
