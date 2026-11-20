"""Keep business questions and complete evidence inside the QA workspace."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import uuid

from .client import Client, Failure
from .hello import discover_workspace

DATABASE = 'journey3_benchmark'
HISTORY = DATABASE + '.results'
SPECS = DATABASE + '.case_specs'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def literal(value):
    if value is None:
        return 'NULL'
    if type(value) in (int, float):
        import math
        if not math.isfinite(value):
            raise ValueError('nonfinite_sql_value')
        return str(value)
    return "'" + str(value).replace('\\', '\\\\').replace("'", "''") + "'"


class Store:
    def __init__(self, client, root=None):
        self.client = client
        self.root = root or discover_workspace(client, 'journey-3')

    def sql(self, statement, limit=1000):
        result = self.client.product('POST', '/query/run',
            {'db_name': DATABASE, 'query': statement, 'limit': limit}, expected_code='OK')
        if result.get('status') != 'success':
            raise Failure('benchmark_store_query_failed')
        result['rows'] = [[cell.get('String') if cell.get('Valid') else None for cell in row]
                          for row in (result.get('result') or [])]
        return result

    def spec(self, case):
        ref = case['private_spec']
        result = self.sql('SELECT spec_json,sha256 FROM ' + SPECS +
            ' WHERE case_id=' + literal(case['id']) + ' AND revision=' + literal(ref['revision']), 2)
        rows = result.get('rows')
        if not isinstance(rows, list) or len(rows) != 1:
            raise Failure('private_case_revision_not_found')
        spec = json.loads(rows[0][0])
        if (digest(spec) != ref['sha256'] or rows[0][1] != ref['sha256'] or
                spec.get('id') != case['id'] or spec.get('workspace') != 'journey-3'):
            raise Failure('private_case_integrity_mismatch')
        return spec

    def dataset(self, identity):
        rows=self.sql('SELECT state,manifest_sha256 FROM '+DATABASE+'.dataset_snapshots WHERE snapshot_id='+
                      literal(identity),2)['rows']
        if len(rows)!=1:raise Failure('benchmark_snapshot_not_found')
        return {'state':rows[0][0],'manifest_sha256':rows[0][1]}

    def accessed_private_evidence(self, candidates):
        # Look up only IDs actually present in tool-call arguments, rather than
        # loading the growing history or treating a file listing as a read.
        values = sorted(set(candidates))
        for offset in range(0, len(values), 500):
            row = self.sql('SELECT COUNT(*) FROM ' + DATABASE + '.private_evidence_files WHERE file_id IN (' +
                ','.join(literal(value) for value in values[offset:offset + 500]) + ')', 1)['rows']
            if len(row) != 1 or len(row[0]) != 1:
                raise Failure('private_evidence_lookup_invalid')
            if int(row[0][0]):
                return True
        return False

    def upload(self, directory, case_id):
        # Only files inside this case, with fixed archive paths; never archive
        # the runner worktree, parent directories, symlinks or credentials.
        directory = Path(directory)
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w:gz') as archive:
            for path in sorted(directory.rglob('*')):
                if path.is_symlink():
                    raise Failure('private_evidence_symlink')
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(directory), recursive=False)
        return self.upload_private(case_id + '-evidence.tar.gz', payload.getvalue(), 'application/gzip')

    def upload_private(self, filename, data, content_type):
        boundary = 'journey-' + uuid.uuid4().hex
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode()
        raw += data + f'\r\n--{boundary}--\r\n'.encode()
        result = self.client.request('POST', self.root + '/catalog-files/uploads', raw=raw,
            content_type='multipart/form-data; boundary=' + boundary)
        file = result.get('data', {})
        file_id = file.get('file_id')
        if result.get('code') != 'OK' or not isinstance(file_id, str) or not file_id:
            raise Failure('private_evidence_upload_failed')
        checksum = hashlib.sha256(data).hexdigest()
        self.sql('INSERT IGNORE INTO ' + DATABASE + '.private_evidence_files(file_id,sha256) VALUES (' +
                 literal(file_id) + ',' + literal(checksum) + ')')
        return {'file_id': file_id, 'sha256': checksum, 'bytes': len(data)}

    def persist(self, result, case, directory):
        from .benchmark_metrics import history_observations, history_record
        events = Path(directory) / 'events.jsonl'
        record = history_record(result, case, events)
        stored = {**result, 'history_observations': history_observations(result, record, events)}
        key = digest([record['run_id'], record['run_attempt'], case['id'], record['case_revision']])
        prior = self.sql('SELECT report_json FROM ' + HISTORY + ' WHERE result_key=' + literal(key), 1)
        if prior.get('rows'):
            previous = json.loads(prior['rows'][0][0])
            # Pipeline completion is written after the immutable execution
            # snapshot; it is not part of the execution's idempotency key.
            previous.pop('pipeline', None)
            if 'history_observations' not in previous:
                # A retry can encounter a record written before observations were added.
                comparable = result
            else:
                comparable = stored
            if digest(previous) != digest(comparable):
                raise Failure('benchmark_history_result_key_conflict')
            return {'state': 'already_saved', 'result_key': key}
        receipt = self.upload(directory, case['id'])
        record.update(result_key=key, evidence_file_id=receipt['file_id'],
                      evidence_sha256=receipt['sha256'], report_json=canonical(stored))
        names = list(record)
        self.sql('INSERT INTO ' + HISTORY + ' (' + ','.join('`'+x+'`' for x in names) + ') VALUES (' +
                 ','.join(literal(record[x]) for x in names) + ')')
        verify = self.sql('SELECT evidence_file_id FROM ' + HISTORY + ' WHERE result_key=' + literal(key), 1)
        if verify.get('rows') != [[receipt['file_id']]]:
            raise Failure('benchmark_history_receipt_mismatch')
        return {'state': 'saved', 'result_key': key}

    def finalize_pipeline(self, result, case, directory, stages):
        from .benchmark_metrics import history_record
        from .failure_categories import STAGES
        if (not isinstance(stages, dict) or set(stages) - STAGES or any(
                not isinstance(value, dict) or type(value.get('timed_out')) is not bool or
                (value.get('exit_code') is not None and type(value['exit_code']) is not int)
                for value in stages.values())):
            raise Failure('invalid_pipeline_stages')
        record = history_record(result, case, Path(directory) / 'events.jsonl')
        key = digest([record['run_id'], record['run_attempt'], case['id'], record['case_revision']])
        condition = ' WHERE result_key=' + literal(key)
        rows = self.sql('SELECT report_json FROM ' + HISTORY + condition, 1)['rows']
        if len(rows) != 1:
            raise Failure('pipeline_history_not_found')
        stored = json.loads(rows[0][0])
        if stored.get('case_id') != case['id'] or stored.get('workspace') != 'journey-3':
            raise Failure('pipeline_history_identity_mismatch')
        pipeline = {'stages': {name: {key: value.get(key) for key in ('exit_code', 'timed_out')}
                               for name, value in stages.items()}}
        if (stored.get('pipeline') or {}).get('stages') == pipeline['stages']:
            return
        # History/publication run after the original archive was captured.
        # Preserve their failure logs separately without replacing that archive.
        logs = Path(directory) / 'pipeline'
        if logs.is_dir() and any(name in {'history', 'publish'} and
                (value['exit_code'] != 0 or value['timed_out']) for name, value in stages.items()):
            pipeline['evidence'] = self.upload(logs, case['id'] + '-pipeline')
        updated = {**stored, 'pipeline': pipeline}
        # Preserve task state, quality, observations and original evidence. Only
        # the post-execution journal is added, guarded by the snapshot we read.
        self.sql('UPDATE ' + HISTORY + ' SET report_json=' + literal(canonical(updated)) + condition +
                 ' AND report_json=' + literal(rows[0][0]))
        verified = self.sql('SELECT report_json FROM ' + HISTORY + condition, 1)['rows']
        if len(verified) != 1 or json.loads(verified[0][0]) != updated:
            raise Failure('pipeline_history_confirmation_failed')


def configured_store(timeout=300):
    return Store(Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], timeout=timeout))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['persist', 'finalize-pipeline'])
    parser.add_argument('case_id')
    args = parser.parse_args()
    from .catalog import load_case
    case = load_case(args.case_id)
    directory = Path('results') / case['id']
    result = json.loads((directory / 'result.json').read_text())
    store = configured_store()
    if args.action == 'finalize-pipeline':
        stages = json.loads((directory / 'batch-stages.json').read_text())
        if stages.get('case_id') != case['id']:
            raise Failure('pipeline_case_identity_mismatch')
        store.finalize_pipeline(result, case, directory, stages['stages'])
        print(json.dumps({'case_id': case['id'], 'pipeline': 'saved'}))
        return
    receipt = store.persist(result, case, directory)
    (directory / 'history-receipt.json').write_text(canonical(receipt))
    print(json.dumps({'case_id': case['id'], 'history': receipt['state']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Remote errors may contain SQL or business data. The full diagnostic
        # stays in the runner's private work area, never in Actions stdout.
        from .client import error_record
        log = os.environ.get('MOI_PRIVATE_STAGE_LOG')
        destination = Path(log).with_suffix('.error.json') if log else Path('history-error.json')
        destination.write_text(canonical(error_record('history',error)))
        print('::error::QA benchmark history persistence failed')
        raise SystemExit(1)
