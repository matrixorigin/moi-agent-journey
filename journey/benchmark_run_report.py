"""Generate the Journey-3 HTML for the target Catalog and GitHub Actions."""
import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from .benchmark_history import RUN_ID, read_metrics, read_run_metadata, render_history
from .benchmark_details import DETAIL_KINDS, details_for_case
from .benchmark_store import HISTORY, configured_store, literal
from .catalog import load_suite
from .evidence import Evidence, configured_secrets
from .production import is_production
from .report import ArtifactRejected, inspect_payload


# Keep a page within the API response limit even when JSON-escaping the text.
REPORT_PAGE_SIZE = 16
INLINE_REPORT_BYTES = 256_000
REPORT_CHUNK_CHARS = 1_000_000


def read_large_report(store, where, length):
    parts = []
    for start in range(1, length + 1, REPORT_CHUNK_CHARS):
        count = min(REPORT_CHUNK_CHARS, length - start + 1)
        result = store.sql(f'SELECT SUBSTRING(report_json,{start},{count})' + where, 1)
        rows = result['rows']
        if (result.get('truncated') or len(rows) != 1 or len(rows[0]) != 1
                or not isinstance(rows[0][0], str) or len(rows[0][0]) != count):
            raise ValueError('truncated_private_report_chunk')
        parts.append(rows[0][0])
    return ''.join(parts)


def run_details(store, run, attempt):
    if not RUN_ID.fullmatch(run) or type(attempt) is not int or attempt < 1:
        raise ValueError('invalid_report_run')
    fields = ['case_id', 'task_state', 'benchmark_state', 'duration_seconds', 'model_round_count',
              'quality_score', 'session_id', 'trace_id']
    details = {'run_id': run, 'attempt': attempt, **{name: [] for name in DETAIL_KINDS}}
    where = ' FROM '+HISTORY+' WHERE run_id='+literal(run)+' AND run_attempt='+literal(attempt)
    inline_report = f'CASE WHEN LENGTH(report_json)<={INLINE_REPORT_BYTES} THEN report_json ELSE NULL END'
    cursor = ''
    while True:
        result = store.sql('SELECT '+','.join([*fields, inline_report, 'CHAR_LENGTH(report_json)'])+where+
                           ' AND case_id>'+literal(cursor)+' ORDER BY case_id LIMIT '+str(REPORT_PAGE_SIZE),
                           REPORT_PAGE_SIZE)
        page = result['rows']
        if result.get('truncated'):
            raise ValueError('truncated_private_report_page')
        for *values, raw, length in page:
            if len(values) != len(fields):
                raise ValueError('invalid_private_report_row')
            row = dict(zip(fields, values))
            if not re.fullmatch(r'J3-\d{2,}', row['case_id'] or '') or row['case_id'] <= cursor:
                raise ValueError('invalid_private_report_case_order')
            cursor = row['case_id']
            for field in ('quality_score', 'duration_seconds'):
                row[field] = None if row[field] is None else float(row[field])
            if raw is None:
                raw = read_large_report(store, where+' AND case_id='+literal(cursor), int(length))
            for kind, records in details_for_case(row, json.loads(raw)).items():
                details[kind].extend(records)
        if len(page) < REPORT_PAGE_SIZE:
            return details


def publish(store, rows, run, attempt, artifact_dir=None, run_metadata=None, *, production=False):
    details = run_details(store, run, attempt)
    planned = load_suite('journey-3')['cases']
    observed = {row['case_id'] for row in rows if row['run_id'] == run and row['run_attempt'] == attempt}
    missing = [case_id for case_id in planned if case_id not in observed]
    details['coverage'] = {'recorded': len(planned) - len(missing), 'planned': len(planned), 'missing': missing}
    details['environment'] = 'Production' if production else 'QA'
    details['report_error_cases'] = len({record[0] for record in details.get('errors', [])})
    with tempfile.TemporaryDirectory() as tmp:
        details = Evidence(tmp, configured_secrets()).clean(details)
    filename = f'journey-3-report-{run}-attempt-{attempt}.html'
    html = render_history(rows, details, run_metadata).encode('utf-8')
    store.upload_private(filename, html, 'text/html; charset=utf-8')
    if artifact_dir is not None:
        if production:
            summary = {key: details[key] for key in ('run_id', 'attempt', 'coverage', 'environment', 'report_error_cases')}
            html = render_history(rows, {**summary, 'metrics_only': True}, run_metadata).encode('utf-8')
        inspect_payload(html, configured_secrets(), name=filename)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / filename).write_bytes(html)
    return filename, missing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-dir', type=Path)
    args = parser.parse_args()
    try:
        store = configured_store()
        run, attempt = os.environ['GITHUB_RUN_ID'], int(os.environ['GITHUB_RUN_ATTEMPT'])
        rows = read_metrics(store)
        metadata = read_run_metadata(rows, os.environ.get('GITHUB_REPOSITORY', ''))
        current_number = os.environ.get('GITHUB_RUN_NUMBER', '')
        if RUN_ID.fullmatch(current_number):
            metadata.setdefault(run, {})['number'] = int(current_number)
        filename, missing = publish(store, rows, run, attempt, args.artifact_dir, metadata,
                                    production=is_production())
        print('Journey-3 HTML saved in the target Catalog')
        if args.artifact_dir is not None and os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write(f'artifact-file={args.artifact_dir / filename}\n')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
                status = '结果记录不完整' if missing else '已收齐结果'
                stream.write(f'\n**Journey-3 HTML（{status}）**\n')
        if missing:
            print('::error::Journey-3 report is missing rows for: ' + ', '.join(missing))
            return 1
    except ArtifactRejected:
        print('::error::Journey-3 HTML was saved in the target Catalog and withheld from GitHub by the credential scan')
        return 1
    except Exception:
        print('::error::Journey-3 could not prepare the HTML report')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
