"""Choose the most recently tested different production release, including failed runs."""
import json
from datetime import datetime
from pathlib import Path


def run_identity(run):
    run = Path(run).resolve()
    manifest = json.loads((run / 'run-manifest.json').read_text())
    batch = json.loads((run / 'batch-result.json').read_text())
    rows = batch.get('cases', [])
    expected = manifest.get('case_count', batch.get('total_cases'))
    if type(expected) is not int or expected <= 0 or len(rows) != expected:
        raise ValueError('release_run_incomplete')
    ids = [row['case_id'] for row in rows]
    if len(set(ids)) != expected:
        raise ValueError('release_run_duplicate_cases')
    versions, times = set(), []
    for cid in ids:
        result = json.loads((run / 'results' / cid / 'result.json').read_text())
        before, after = result.get('qa_version_start'), result.get('qa_version_end')
        if (not isinstance(before, dict) or before != after or
                not before.get('version') or not before.get('git_commit')):
            raise ValueError('release_version_unverified')
        versions.add((before['version'], before['git_commit']))
        tested_at = result.get('finished_at') or result.get('finalized_at')
        if tested_at:
            times.append(datetime.fromisoformat(tested_at.replace('Z', '+00:00')))
    if len(versions) != 1:
        raise ValueError('release_changed_during_run')
    completed = batch.get('completed_at')
    if completed and not times:
        times.append(datetime.fromisoformat(completed.replace('Z', '+00:00')))
    if not times or any(t.tzinfo is None for t in times):
        raise ValueError('release_run_timestamp_missing')
    return {'path': str(run), 'version': next(iter(versions)), 'tested_at': max(times),
            'environment': batch.get('environment'), 'workspace_id': batch.get('workspace_id'),
            'model': batch.get('model'), 'suite_sha256': manifest.get('suite_sha256')}


def select_previous_release(current_run, history_root):
    current = run_identity(current_run)
    candidates, skipped = [], []
    for path in sorted(Path(history_root).glob('*/batch-result.json')):
        run = path.parent.resolve()
        if str(run) == current['path']:
            continue
        try:
            other = run_identity(run)
            reason = None
            if any(not current.get(k) or other.get(k) != current[k] for k in
                   ('environment', 'workspace_id', 'model', 'suite_sha256')):
                reason = 'execution_contract_changed'
            elif other['version'] == current['version']:
                reason = 'same_release'
            elif other['tested_at'] >= current['tested_at']:
                reason = 'not_earlier_than_current'
            if reason:
                skipped.append({'run': str(run), 'reason': reason})
            else:
                candidates.append(other)
        except (ValueError, KeyError, TypeError, OSError) as error:
            skipped.append({'run': str(run), 'reason': str(error)})
    selected = max(candidates, key=lambda c: (c['tested_at'], c['path']), default=None)
    return {'policy': 'latest_completed_test_of_a_different_release',
            'selected_run': selected['path'] if selected else None,
            'current_version': list(current['version']),
            'baseline_version': list(selected['version']) if selected else None,
            'skipped': skipped}
