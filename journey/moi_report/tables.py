"""Projection of the J2 comparison contract into native dashboard table inputs."""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path


SCHEMA = 'j2-dashboard-tables-v1'
OPTIONAL_TABLES = ('v2_summary', 'v2_details', 'path_scoring', 'cause_summary', 'case_attribution')


def bps(value):
    return None if value is None else int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def millis(value):
    return None if type(value) not in (int, float) else round(value * 1000)


def compact(value, maximum=1900):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= maximum else text[:maximum] + ' [完整内容见原始证据]'


def validate_payload(value):
    if (not isinstance(value, dict) or value.get('schema_version') != SCHEMA or
            not isinstance(value.get('scope'), dict) or not isinstance(value.get('tables'), dict)):
        raise ValueError('invalid_dashboard_table_contract')
    required = {'case_results', 'module_summary', 'overview', 'duration_curve',
                'failed_checks', 'provenance', *OPTIONAL_TABLES}
    if set(value['tables']) != required:
        raise ValueError('dashboard_table_set_mismatch')
    for name, rows in value['tables'].items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid_dashboard_table:' + name)
        fields = [set(row) for row in rows]
        if fields and any(item != fields[0] for item in fields):
            raise ValueError('dashboard_table_column_mismatch:' + name)
    return value


def from_comparison(report):
    """Keep source scores, coverage and comparisons separate from presentation."""
    empty = {'v2_summary': [], 'v2_details': [], 'cause_summary': [], 'case_attribution': []}
    cases = []
    failures = []
    for row in report['cases']:
        current, baseline = row['current'], row['baseline']
        original = current.get('tasks') or []
        cases.append({
            'case_id': row['case_id'], 'module_key': row['domain'],
            'module_name': next(m['label'] for m in report['modules'] if m['domain'] == row['domain']),
            'batch_label': row.get('batch_label', report['current']['run']), 'title': row['title'],
            'verdict': current.get('verdict') or current.get('excluded_reason'),
            'baseline_bps': bps(baseline.get('score')), 'current_bps': bps(current.get('score')),
            'delta_bps': bps(row.get('score_delta_current_minus_baseline')),
            'comparability': row['comparison_status'], 'passed_checks': current.get('passed_checks'),
            'expected_checks': current.get('expected_checks'),
            'functional_bps': bps(current.get('functional_score') if current.get('functional_score') is not None else current.get('score')),
            'path_factor_bps': bps(current.get('path_factor') or '1'),
            'path_classification': current.get('path_classification') or 'original_functional',
            'scoring_policy': current.get('scoring_policy') or 'functional_v1',
            'baseline_ms': millis(baseline.get('task_seconds')), 'current_ms': millis(current.get('task_seconds')),
            'duration_delta_ms': millis(row.get('task_seconds_delta_current_minus_baseline')),
            'task_id': original[-1].get('task_id', '') if original else '',
            'trace_id': original[-1].get('trace_id', '') if original else '',
            'version': (current.get('qa_version_start') or {}).get('version', 'unknown'),
            'excluded_reason': current.get('excluded_reason'), 'result_sha256': current.get('result_sha256'),
        })
        for check in current.get('checkpoints', []):
            if check.get('status') == 'failed':
                failures.append({'case_id': row['case_id'], 'module_name': cases[-1]['module_name'],
                    'session_name': str(check.get('session') or ''), 'rule_text': compact(check.get('rule')),
                    'detail_text': compact(check.get('detail')), 'evidence_origin': '原始结果',
                    'unreached': int(check.get('detail') == 'checkpoint_not_reached_or_unavailable')})
    stats = report['current']
    total = len(cases)
    thresholds = sorted({0, 25, 50, 75, 100, 150, 200, 300, 450, 600,
                         *[__import__('math').ceil(row[side]['task_seconds']) for row in report['cases']
                           for side in ('baseline', 'current') if type(row[side].get('task_seconds')) in (int, float)]})
    tables = {
        'case_results': cases,
        'module_summary': [{'module_name': m['label'], 'case_count': m['total'],
            'baseline_scored': m['baseline_scored'], 'current_scored': m['current_scored'],
            'coverage_bps': round(m['current_scored'] * 10000 / m['total']),
            'baseline_bps': bps(m['baseline_score']), 'current_bps': bps(m['current_score']),
            'paired_count': m['paired_count'], 'paired_baseline_bps': bps(m['paired_baseline_score']),
            'paired_current_bps': bps(m['paired_current_score']), 'delta_bps': bps(m['paired_delta'])}
                           for m in report['modules']],
        'overview': [{'selected_cases': total, 'active_suite_cases': total, 'remaining_cases': 0,
            'module_count': len(report['modules']), 'scored_cases': stats['scored'],
            'coverage_bps': bps(stats['coverage']), 'comparable_cases': report['comparability']['fully_comparable_scored_cases'],
            'pass_cases': stats['passed'], 'partial_cases': stats['partial'], 'fail_cases': stats['failed'],
            'blocked_cases': stats['blocked'], 'framework_errors': stats['framework_errors'],
            'timeout_cases': stats['timeouts'],
            'within_100_bps': round(report['duration_thresholds']['current']['within_100_seconds'] * 10000 / total),
            'within_200_bps': round(report['duration_thresholds']['current']['within_200_seconds'] * 10000 / total)}],
        'duration_curve': [{'threshold_seconds': t, **{side + '_bps': round(sum(
            type(row[side].get('task_seconds')) in (int, float) and row[side]['task_seconds'] <= t
            for row in report['cases']) * 10000 / total) for side in ('baseline', 'current')}} for t in thresholds],
        'failed_checks': failures,
        'provenance': [{'batch_label': report['current']['run'], 'source_run': report['current']['run'],
            'case_count': total, 'model_name': report['model'], 'versions': ' / '.join(stats['versions']),
            'scope_note': '按既有结果投影；不自动判回退'}],
    }
    tables['path_scoring'] = [{k: row[k] for k in ('case_id', 'functional_bps', 'path_factor_bps',
        'current_bps', 'path_classification', 'scoring_policy')} for row in cases
        if row['scoring_policy'] not in ('functional_v1', 'j2-checkpoints-equal-v1')]
    tables.update(empty)
    return validate_payload({'schema_version': SCHEMA,
        'scope': {'suite': report['suite_id'], 'source': report['current']['run']}, 'tables': tables,
        'semantic_limits': ['Scores and exclusions retain source values; no MOI overall quality score.']})


def write_inputs(report_path, output):
    report = json.loads(Path(report_path).read_text())
    payload = from_comparison(report)
    destination = Path(output)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'dashboard-tables.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return destination / 'dashboard-tables.json'
