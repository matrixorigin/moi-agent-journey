"""Export only numeric historical metrics; business evidence remains in QA."""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from .benchmark_store import configured_store, HISTORY, literal
from .catalog import load_suite, plan_suite

FIELDS = ['case_id','evaluated_at','duration_seconds','quality_score','tool_call_count',
          'model_round_count','task_state','case_revision','snapshot_id','evaluator_version',
          'model_key','benchmark_state']
SELECT_FIELDS = [field if field != 'model_key' else 'MD5(model) AS model_key' for field in FIELDS]


def utc_timestamp(value):
    # query/run currently formats DATETIME as Go's UTC time.String(), while
    # saved metric artifacts use ISO 8601. Accept precisely these UTC forms.
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?) \+0000 UTC', value)
    timestamp = datetime.fromisoformat(match[1] + '+00:00' if match else value)
    if timestamp.utcoffset() not in (None, timezone.utc.utcoffset(timestamp)):
        raise ValueError('history_requires_UTC')
    return timestamp.replace(tzinfo=timezone.utc)


def metrics_row(row):
    if len(row) != len(FIELDS) or not re.fullmatch(r'J3-\d{2,}', row[0] or ''):
        raise ValueError('invalid_history_case_identity')
    value = dict(zip(FIELDS, row))
    value['evaluated_at'] = utc_timestamp(value['evaluated_at']).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    for key in ('duration_seconds','quality_score','tool_call_count','model_round_count'):
        if type(value[key]) is bool:
            raise ValueError('invalid_history_metric')
        number = None if value[key] is None else float(value[key])
        if number is not None and (not math.isfinite(number) or number < 0 or
                (key == 'quality_score' and number > 100)):
            raise ValueError('invalid_history_metric')
        value[key] = number
        if number is not None and key in ('tool_call_count', 'model_round_count') and not number.is_integer():
            raise ValueError('invalid_history_count')
    if value['task_state'] not in {'completed','abnormal','timeout','unknown','not_delivered','canceled'}:
        raise ValueError('invalid_history_task_state')
    for key in ('case_revision','snapshot_id','evaluator_version'):
        if value[key] is not None and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value[key]):
            raise ValueError('invalid_history_revision')
    if not re.fullmatch(r'[a-f0-9]{32}', value['model_key'] or ''):
        raise ValueError('invalid_history_model_identity')
    if value['benchmark_state'] not in {'eligible', 'blocked', 'out_of_scope'}:
        raise ValueError('invalid_history_benchmark_state')
    return value


def export(store, destination):
    # This query deliberately has no question, agent name, answer, SQL output,
    # error text or source excerpts. Unknown metric shapes fail publication.
    rows, cursor = [], None
    while True:
        timestamp = None if cursor is None else cursor[0].replace(tzinfo=None).isoformat(sep=' ', timespec='microseconds')
        condition = '' if cursor is None else (' WHERE evaluated_at>'+literal(timestamp)+
            ' OR (evaluated_at='+literal(timestamp)+' AND result_key>'+literal(cursor[1])+')')
        response=store.sql('SELECT result_key,'+','.join(SELECT_FIELDS)+' FROM '+HISTORY+condition+
                           ' ORDER BY evaluated_at,result_key LIMIT 2000',2000)
        page=response['rows']
        if response.get('truncated') or any(not re.fullmatch(r'[a-f0-9]{64}', row[0] or '') for row in page):
            raise ValueError('invalid_history_page')
        rows.extend(metrics_row(row[1:]) for row in page)
        if len(page)<2000:break
        following=(utc_timestamp(page[-1][2]),page[-1][0])
        if cursor is not None and following<=cursor:raise ValueError('nonprogressing_history_cursor')
        cursor=following
    write_metrics(rows, destination)
    return len(rows)


def write_metrics(rows, destination):
    # Revalidate imported historical artifacts; never copy their HTML or fields.
    checked = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError('invalid_exported_history_columns')
        checked.append(metrics_row([row[key] for key in FIELDS]))
    rows = checked
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'history.json').write_text(json.dumps(rows, ensure_ascii=True, indent=2))
    with (destination/'history.csv').open('w', newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    plan = plan_suite(load_suite('journey-3'))
    jobs = {job['case']: job['case_ids'] for job in plan['include'] if job['executor'] == 'batch'}
    template = (Path(__file__).parent.parent / 'datasets/journey-3/history.html').read_text()
    html = template.replace('/*HISTORY_DATA*/[]', json.dumps(rows, ensure_ascii=True).replace('<', '\\u003c'))
    html = html.replace('/*HISTORY_JOBS*/{}', json.dumps(jobs, ensure_ascii=True).replace('<', '\\u003c'))
    (destination / 'history.html').write_text(html)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out', required=True);args=parser.parse_args()
    try:
        count=export(configured_store(),args.out)
    except Exception:
        print('::error::Journey-3 history metrics export failed; no business data was published')
        return 1
    print(json.dumps({'history_metric_rows':count}));return 0


if __name__=='__main__':raise SystemExit(main())
