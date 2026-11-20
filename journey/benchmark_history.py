"""Export only numeric historical metrics; business evidence remains in QA."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess

from .benchmark_metrics import LEGACY_TIMING_FIELDS, history_observations
from .benchmark_store import configured_store, HISTORY, literal
from .catalog import load_suite, plan_suite

BASE_FIELDS = ['case_id','evaluated_at','duration_seconds','quality_score','tool_call_count',
          'model_round_count','task_state','case_revision','snapshot_id','evaluator_version',
          'model_key','benchmark_state','run_id','run_attempt']
OBSERVATION_FIELDS = ['qa_outcome', 'completed_model_rounds', 'tool_succeeded', 'tool_failed']
FIELDS = BASE_FIELDS + OBSERVATION_FIELDS
SELECT_FIELDS = [field if field != 'model_key' else 'MD5(model) AS model_key' for field in BASE_FIELDS]
OBSERVATIONS_SQL = "JSON_EXTRACT(report_json,'$.history_observations')"
RUN_ID = re.compile(r'[1-9][0-9]{0,23}')


def utc_timestamp(value):
    # query/run currently formats DATETIME as Go's UTC time.String(), while
    # saved metric artifacts use ISO 8601. Accept precisely these UTC forms.
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))? \+0000 UTC', value)
    wire = match[1] + '.' + (match[2] or '').ljust(6, '0') + '+00:00' if match else value
    timestamp = datetime.fromisoformat(wire.replace('Z', '+00:00'))
    if timestamp.utcoffset() not in (None, timezone.utc.utcoffset(timestamp)):
        raise ValueError('history_requires_UTC')
    return timestamp.replace(tzinfo=timezone.utc)


def metrics_row(row):
    if len(row) != len(FIELDS) or not re.fullmatch(r'J3-\d{2,}', row[0] or ''):
        raise ValueError('invalid_history_case_identity')
    value = dict(zip(FIELDS, row))
    value['evaluated_at'] = utc_timestamp(value['evaluated_at']).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    numbers = ('duration_seconds','quality_score','tool_call_count','model_round_count',
               'completed_model_rounds','tool_succeeded','tool_failed','run_attempt')
    for key in numbers:
        if type(value[key]) is bool:
            raise ValueError('invalid_history_metric')
        number = None if value[key] is None else float(value[key])
        if number is not None and (not math.isfinite(number) or number < 0 or
                (key == 'quality_score' and number > 100)):
            raise ValueError('invalid_history_metric')
        value[key] = number
        if number is not None and key not in ('duration_seconds','quality_score'):
            if not number.is_integer():
                raise ValueError('invalid_history_count')
            value[key] = int(number)
    if value['task_state'] not in {'completed','abnormal','timeout','unknown','not_delivered','canceled'}:
        raise ValueError('invalid_history_task_state')
    for key in ('case_revision','snapshot_id','evaluator_version'):
        if value[key] is not None and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value[key]):
            raise ValueError('invalid_history_revision')
    if not re.fullmatch(r'[a-f0-9]{32}', value['model_key'] or ''):
        raise ValueError('invalid_history_model_identity')
    if value['benchmark_state'] not in {'eligible', 'blocked', 'out_of_scope'}:
        raise ValueError('invalid_history_benchmark_state')
    if not RUN_ID.fullmatch(value['run_id'] or '') or not value['run_attempt']:
        raise ValueError('invalid_history_run')
    if value['qa_outcome'] not in {'completed', 'error', 'timeout', 'excluded'}:
        raise ValueError('invalid_history_qa_outcome')
    return value


def read_metrics(store):
    # Historical rows written before observations existed are projected in
    # memory. No private fields from those reports enter the export.
    rows, cursor = [], None
    page_size = 100
    while True:
        timestamp = None if cursor is None else cursor[0].replace(tzinfo=None).isoformat(sep=' ', timespec='microseconds')
        condition = '' if cursor is None else (' AND (evaluated_at>'+literal(timestamp)+
            ' OR (evaluated_at='+literal(timestamp)+' AND result_key>'+literal(cursor[1])+'))')
        response=store.sql('SELECT result_key,'+','.join(SELECT_FIELDS)+', '+OBSERVATIONS_SQL+
                           ', CASE WHEN '+OBSERVATIONS_SQL+' IS NULL THEN report_json ELSE NULL END'
                           ' FROM '+HISTORY+" WHERE run_id REGEXP '^[1-9][0-9]{0,23}$'"+condition+
                           ' ORDER BY evaluated_at,result_key LIMIT '+str(page_size),page_size)
        page=response['rows']
        if response.get('truncated') or any(not re.fullmatch(r'[a-f0-9]{64}', row[0] or '') for row in page):
            raise ValueError('invalid_history_page')
        for row in page:
            values = row[1:1+len(BASE_FIELDS)]
            saved, legacy = row[-2:]
            if saved is not None:
                observations = json.loads(saved)
                if not isinstance(observations, dict):
                    raise ValueError('invalid_saved_history_observations')
                if set(observations) - LEGACY_TIMING_FIELDS != set(OBSERVATION_FIELDS):
                    raise ValueError('invalid_saved_history_observations')
            else:
                observations = history_observations(json.loads(legacy), dict(zip(BASE_FIELDS, values)))
            rows.append(metrics_row(values + [observations[key] for key in OBSERVATION_FIELDS]))
        if len(page)<page_size:break
        following=(utc_timestamp(page[-1][2]),page[-1][0])
        if cursor is not None and following<=cursor:raise ValueError('nonprogressing_history_cursor')
        cursor=following
    return rows


def export(store, destination):
    rows = read_metrics(store)
    write_metrics(rows, destination)
    return len(rows)


def read_run_metadata(rows, repository):
    ids = {row['run_id'] for row in rows}
    if not ids or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        return {}
    try:
        result = subprocess.run(
            ['gh', 'api', '--paginate', f'repos/{repository}/actions/workflows/journeys.yml/runs?per_page=100',
             '--jq', '.workflow_runs[] | {id, run_number, run_attempt, created_at, run_started_at} | @json'],
            check=True, capture_output=True, text=True, timeout=30)
        metadata = {}
        for line in result.stdout.splitlines():
            entry = json.loads(line)
            run_id = str(entry['id'])
            if run_id not in ids:
                continue
            number, attempt = entry['run_number'], entry['run_attempt']
            if type(number) is not int or number < 1 or type(attempt) is not int or attempt < 1:
                continue
            metadata[run_id] = {'number': number, 'attempt': attempt,
                'created_at': utc_timestamp(entry['created_at']).isoformat(),
                'started_at': utc_timestamp(entry['run_started_at']).isoformat() if entry.get('run_started_at') else None}
        return metadata
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        print('::warning::GitHub run metadata was unavailable; the chart can use run IDs and QA timestamps')
        return {}


def render_history(rows, current=None, run_metadata=None, *, pages_environment=None):
    plan = plan_suite(load_suite('journey-3'))
    jobs = {job['case']: job['case_ids'] for job in plan['include'] if job['executor'] == 'batch'}
    template = (Path(__file__).parent.parent / 'datasets/journey-3/history.html').read_text()
    values = {'/*HISTORY_DATA*/[]': rows, '/*HISTORY_JOBS*/{}': jobs, '/*RUN_DETAILS*/null': current,
              '/*RUN_METADATA*/{}': run_metadata or {}, '/*PAGES_ENVIRONMENT*/null': pages_environment}
    # Replace once in the template, so a marker inside evidence remains text.
    return re.sub('|'.join(re.escape(key) for key in values),
                  lambda match: json.dumps(values[match[0]], ensure_ascii=True).replace('<', '\\u003c'),
                  template)


def write_metrics(rows, destination):
    # Revalidate imported historical artifacts; never copy their HTML or fields.
    checked = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - LEGACY_TIMING_FIELDS != set(FIELDS):
            raise ValueError('invalid_exported_history_columns')
        checked.append(metrics_row([row[key] for key in FIELDS]))
    rows = checked
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'history.json').write_text(json.dumps(rows, ensure_ascii=True, indent=2))
    with (destination/'history.csv').open('w', newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    (destination / 'history.html').write_text(render_history(rows))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out', required=True);args=parser.parse_args()
    try:
        count=export(configured_store(),args.out)
    except Exception:
        print('::error::Journey-3 history metrics export failed; no business data was published')
        return 1
    print(json.dumps({'history_metric_rows':count}));return 0


if __name__=='__main__':raise SystemExit(main())
