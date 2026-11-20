"""Build the two public Journey-3 pages from per-environment snapshots."""
import argparse
import json
from pathlib import Path
from shutil import copyfile

from .benchmark_details import DETAIL_KINDS
from .benchmark_history import FIELDS, render_history
from .evidence import Evidence, configured_secrets
from .report import inspect_payload


ENVIRONMENTS = {'qa': 'QA', 'production': 'Production'}
SUMMARY_FIELDS = ('run_id', 'attempt', 'coverage', 'report_error_cases')
RUN_FIELDS = ('number', 'attempt', 'created_at', 'started_at')


def write_snapshot(path, environment, rows, summary, metadata, *, details=None):
    """Stage history metrics and the four approved views for the latest run."""
    if not summary['coverage']['recorded']:
        return False
    current = {key: summary[key] for key in SUMMARY_FIELDS}
    current['coverage'] = {key: summary['coverage'][key] for key in ('recorded', 'planned', 'missing')}
    secrets = configured_secrets()
    if details is not None:
        latest = {row['case_id']: row for row in rows
                  if row['run_id'] == current['run_id'] and row['run_attempt'] == current['attempt']}
        included = {
            'errors': {case for case, row in latest.items() if row['qa_outcome'] == 'error'},
            'timeouts': {case for case, row in latest.items() if row['qa_outcome'] == 'timeout'},
            'tools': latest.keys(),
            'scores': {case for case, row in latest.items() if row['qa_outcome'] == 'completed'
                       and row['quality_score'] is not None and row['quality_score'] < 50},
        }
        widths = {'errors': 4, 'timeouts': 4, 'tools': 6, 'scores': 5}
        public_details = {kind: [record[:widths[kind]] for record in details[kind] if record[0] in included[kind]]
                          for kind in DETAIL_KINDS}
        current.update(Evidence(path.parent, secrets).clean(public_details))
    snapshot = {
        'environment': environment,
        'current': current,
        'rows': [{key: row[key] for key in FIELDS} for row in rows],
        'metadata': {run_id: {key: run[key] for key in RUN_FIELDS if key in run}
                     for run_id, run in metadata.items()},
    }
    snapshot_version(snapshot, environment)
    payload = json.dumps(snapshot, ensure_ascii=True, separators=(',', ':')).encode()
    inspect_payload(payload, secrets, name=path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return True


def snapshot_version(snapshot, environment):
    if environment not in ENVIRONMENTS or snapshot['environment'] != environment:
        raise ValueError('Journey-3 snapshot was generated for the wrong environment')
    current = snapshot['current']
    number = snapshot['metadata'][current['run_id']]['number']
    attempt = current['attempt']
    if any(type(value) is not int or value < 1 for value in (number, attempt)):
        raise ValueError('Journey-3 snapshot is missing a valid GitHub run number or attempt')
    return number, attempt


def build_site(state, output, *, incoming=None, environment=None):
    snapshots = {}
    for key in ENVIRONMENTS:
        path = state / f'{key}.json'
        if path.exists():
            snapshots[key] = json.loads(path.read_bytes())
            snapshot_version(snapshots[key], key)
    if incoming is not None:
        snapshot = json.loads(incoming.read_bytes())
        new_version = snapshot_version(snapshot, environment)
        old = snapshots.get(environment)
        if old is None or new_version > snapshot_version(old, environment):
            snapshots[environment] = snapshot
            copyfile(incoming, state / f'{environment}.json')
        else:
            print(f'Using the stored {ENVIRONMENTS[environment]} snapshot for the same or a newer run')

    output.mkdir(parents=True, exist_ok=True)
    copyfile(Path(__file__).parent.parent / 'datasets/journey-3/pages-index.html', output / 'index.html')
    for key, label in ENVIRONMENTS.items():
        path = output / key / 'index.html'
        path.parent.mkdir(exist_ok=True)
        snapshot = snapshots.get(key)
        if snapshot:
            current = snapshot['current']
            summary = {**current, 'environment': label, 'metrics_only': not all(kind in current for kind in DETAIL_KINDS)}
            rows = [{key: row[key] for key in FIELDS} for row in snapshot['rows']]
            html = render_history(rows, summary, snapshot['metadata'], pages_environment=label)
        else:
            html = render_history([], pages_environment=label)
        path.write_text(html, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--incoming', type=Path)
    parser.add_argument('--environment', choices=ENVIRONMENTS)
    args = parser.parse_args()
    if (args.incoming is None) != (args.environment is None):
        parser.error('--incoming and --environment must be provided together')
    build_site(args.state, args.out, incoming=args.incoming, environment=args.environment)


if __name__ == '__main__':
    main()
