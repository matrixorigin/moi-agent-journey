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
        data = payload.getvalue()
        boundary = 'journey-' + uuid.uuid4().hex
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="{case_id}-evidence.tar.gz"\r\nContent-Type: application/gzip\r\n\r\n').encode()
        raw += data + f'\r\n--{boundary}--\r\n'.encode()
        result = self.client.request('POST', self.root + '/catalog-files/uploads', raw=raw,
            content_type='multipart/form-data; boundary=' + boundary)
        file = result.get('data', {})
        file_id = file.get('file_id')
        if result.get('code') != 'OK' or not isinstance(file_id, str) or not file_id:
            raise Failure('private_evidence_upload_failed')
        return {'file_id': file_id, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

    def persist(self, result, case, directory):
        from .benchmark_metrics import history_record
        record = history_record(result, case, Path(directory) / 'events.jsonl')
        key = digest([record['run_id'], record['run_attempt'], case['id'], record['case_revision']])
        prior = self.sql('SELECT report_json FROM ' + HISTORY + ' WHERE result_key=' + literal(key), 1)
        if prior.get('rows'):
            if digest(json.loads(prior['rows'][0][0])) != digest(result):
                raise Failure('benchmark_history_result_key_conflict')
            return {'state': 'already_saved', 'result_key': key}
        receipt = self.upload(directory, case['id'])
        record.update(result_key=key, evidence_file_id=receipt['file_id'],
                      evidence_sha256=receipt['sha256'], report_json=canonical(result))
        names = list(record)
        self.sql('INSERT INTO ' + HISTORY + ' (' + ','.join('`'+x+'`' for x in names) + ') VALUES (' +
                 ','.join(literal(record[x]) for x in names) + ')')
        verify = self.sql('SELECT evidence_file_id FROM ' + HISTORY + ' WHERE result_key=' + literal(key), 1)
        if verify.get('rows') != [[receipt['file_id']]]:
            raise Failure('benchmark_history_receipt_mismatch')
        return {'state': 'saved', 'result_key': key}


def configured_store(timeout=300):
    return Store(Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], timeout=timeout))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['persist'])
    parser.add_argument('case_id')
    args = parser.parse_args()
    from .catalog import load_case
    case = load_case(args.case_id)
    directory = Path('results') / case['id']
    result = json.loads((directory / 'result.json').read_text())
    receipt = configured_store().persist(result, case, directory)
    (directory / 'history-receipt.json').write_text(canonical(receipt))
    print(json.dumps({'case_id': case['id'], 'history': receipt['state']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Remote errors may contain SQL or business data. The full diagnostic
        # stays in the runner's private work area, never in Actions stdout.
        from .client import error_record
        Path('history-error.json').write_text(canonical(error_record('history',error)))
        print('::error::QA benchmark history persistence failed')
        raise SystemExit(1)
