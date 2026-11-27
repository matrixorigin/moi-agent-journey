"""Create and verify native MOI report tables/charts with an explicit resume ledger."""
import hashlib
import json
import re
import time
from pathlib import Path

from .config import specs


SCHEMA = 'moi-dashboard-publication-v1'


def literal(value):
    if value is None:
        return 'NULL'
    if type(value) is bool or not isinstance(value, (str, int, float)):
        raise ValueError('unsupported_dashboard_cell_type')
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def quoted(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', identifier):
        raise ValueError('invalid_dashboard_identifier')
    return '`' + identifier + '`'


class Publication:
    def __init__(self, client, payload, *, database_name, title, description, state_path):
        from .tables import validate_payload
        if (not isinstance(database_name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{2,62}', database_name) or
                not isinstance(title, str) or not title.strip() or not isinstance(description, str) or len(description) > 4000):
            raise ValueError('invalid_publication_configuration')
        self.client, self.payload = client, validate_payload(payload)
        self.database_name, self.title, self.description = database_name, title, description
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = {'schema': SCHEMA, 'stage': 'initial', 'database_name': database_name,
                      'report_scope_sha256': self.digest(payload['scope']), 'tables': {}, 'charts': []}

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def save(self, stage):
        self.state['stage'] = stage
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(self.state_path)

    def resume(self):
        if not self.state_path.exists():
            return False
        self.state = json.loads(self.state_path.read_text())
        if (self.state.get('schema') != SCHEMA or self.state.get('database_name') != self.database_name or
                self.state.get('report_scope_sha256') != self.digest(self.payload['scope'])):
            raise ValueError('publication_resume_scope_mismatch')
        if self.state.get('stage') == 'completed':
            return True
        return False

    def api(self, label, method, path, body=None):
        self.save('submitted:' + label)
        value = self.client.product(method, path, body)
        self.save('received:' + label)
        return value

    def table(self, name):
        return quoted(self.database_name) + '.' + quoted(name)

    def create_tables(self):
        catalogs = self.api('catalogs', 'POST', '/catalog/list', {'include_stats': False})['list']
        matches = [item for item in catalogs if item.get('name') == 'default']
        if len(matches) != 1:
            raise ValueError('default_catalog_not_unique')
        self.state['catalog_id'] = matches[0]['id']
        databases = self.api('databases', 'POST', '/catalog/database/list', {'id': self.state['catalog_id'], 'include_stats': False})['list']
        if self.state.get('database_id'):
            if len([d for d in databases if d.get('id') == self.state['database_id'] and d.get('name') == self.database_name]) != 1:
                raise ValueError('resume_database_identity_not_verified')
        else:
            if any(d.get('name') == self.database_name for d in databases):
                raise ValueError('database_name_already_exists_use_explicit_resume')
            created = self.api('database', 'POST', '/catalog/database/create', {
                'catalog_id': self.state['catalog_id'], 'name': self.database_name,
                'description': 'J2 report tables; retain scoring provenance and coverage.'})
            self.state['database_id'] = created['id']
        self.save('database_verified')

    def materialize(self, name, rows):
        record = self.state['tables'].get(name, {})
        if not rows:
            self.state['tables'][name] = {'status': 'empty_not_materialized', 'rows': 0}
            return
        fields = list(rows[0])
        columns = []
        for field in fields:
            quoted(field)
            values = [row[field] for row in rows if row[field] is not None]
            if values and all(type(value) is int for value in values):
                kind = 'BIGINT'
            elif values and all(type(value) in (int, float) for value in values):
                kind = 'DOUBLE'
            else:
                length = max([len(str(v)) for v in values] or [1])
                width = next((n for n in (128, 512, 2048, 4096) if n >= length), None)
                if width is None:
                    raise ValueError('display_field_too_long:' + name + '.' + field)
                kind = 'VARCHAR(' + str(width) + ')'
            columns.append({'name': field, 'type': kind})
        if not record.get('id'):
            created = self.api('table-' + name, 'POST', '/catalog/table/create', {
                'database_id': self.state['database_id'], 'name': name, 'columns': columns})
            record = {'id': created['id'], 'rows': len(rows), 'fields': fields, 'columns': columns}
            self.state['tables'][name] = record
        if (record.get('fields') != fields or record.get('rows') != len(rows) or record.get('columns') != columns):
            raise ValueError('report_table_schema_changed_use_new_database:' + name)
        children = self.api('children-' + name, 'POST', '/catalog/database/children', {'id': self.state['database_id']}).get('list', [])
        if len([c for c in children if c.get('id') == record['id'] and c.get('name') == name and c.get('type') == 'table']) != 1:
            raise ValueError('resume_table_identity_not_verified:' + name)
        next_offset = record.get('next_offset', 0)
        for offset in range(next_offset, len(rows), 80):
            batch = rows[offset:offset + 80]
            columns_text = ','.join(quoted(field) for field in fields)
            values = ','.join('(' + ','.join(literal(row[field]) for field in fields) + ')' for row in batch)
            result = self.api('insert-' + name + '-' + str(offset), 'POST', '/query/run', {
                'db_name': self.database_name,
                'query': 'INSERT INTO ' + self.table(name) + ' (' + columns_text + ') VALUES ' + values,
                'limit': 100})
            if result.get('status') != 'success':
                raise ValueError('insert_not_successful:' + name)
            record['next_offset'] = offset + len(batch)
            self.save('inserted:' + name)
        names = ','.join(quoted(f) for f in fields)
        nulls = ','.join('CASE WHEN ' + quoted(f) + ' IS NULL THEN 1 ELSE 0 END AS `null_' + f + '`' for f in fields)
        readback = self.api('verify-' + name, 'POST', '/catalog/nl2sql/run_sql', {
            'database_id': self.state['database_id'],
            'sql': 'SELECT ' + names + ',' + nulls + ' FROM ' + self.table(name)})
        actual = []
        for row in readback.get('rows', []):
            if len(row) != len(fields) * 2:
                raise ValueError('null_aware_readback_shape_mismatch:' + name)
            actual.append([None if str(row[i + len(fields)]) == '1' else str(row[i]) for i in range(len(fields))])
        normalize = lambda values: sorted(json.dumps([None if v is None else str(v) for v in row], ensure_ascii=False) for row in values)
        if normalize(actual) != normalize([[row[f] for f in fields] for row in rows]):
            raise ValueError('materialized_rows_mismatch:' + name)
        record['verified'] = True
        self.save('verified:' + name)

    def verify_charts(self):
        pending = {c['id']: c for c in self.state['charts'] if c['id'] not in self.state.get('chart_results', {})}
        deadline = time.monotonic() + 180
        while pending and time.monotonic() < deadline:
            for chart_id, chart in list(pending.items()):
                result = self.client.product('GET', '/data-dashboards/' + self.state['dashboard_id'] + '/charts/' + chart_id + '/result')
                if result.get('status') not in {'completed', 'failed', 'cancelled'}:
                    continue
                rows = (result.get('result') or {}).get('total')
                if (result['status'] != 'completed' or chart['expected_rows'] is not None and
                        int(rows or 0) != chart['expected_rows']):
                    raise ValueError('chart_result_verification_failed:' + chart['name'])
                self.state.setdefault('chart_results', {})[chart_id] = {'status': result['status'], 'rows': rows}
                self.save('chart_verified:' + chart_id)
                del pending[chart_id]
            if pending:
                time.sleep(2)
        if pending:
            raise TimeoutError('chart_results_pending')

    def publish(self):
        if self.resume():
            return self.state
        self.create_tables()
        for name, rows in self.payload['tables'].items():
            if self.state['tables'].get(name, {}).get('verified'):
                continue
            self.materialize(name, rows)
        if not self.state.get('dashboard_id'):
            created = self.api('dashboard', 'POST', '/data-dashboards', {
                'name': self.title, 'description': self.description,
                'sources': [{'table_id': item['id']} for item in self.state['tables'].values() if item.get('id')]})
            self.state['dashboard_id'] = created['ID']
        dashboard = self.api('dashboard-readback', 'GET', '/data-dashboards/' + self.state['dashboard_id'])
        if dashboard.get('ID') != self.state['dashboard_id']:
            raise ValueError('dashboard_identity_mismatch')
        for spec in specs(self.table):
            expected = spec['expected_rows']
            expected = len(self.payload['tables'][expected]) if isinstance(expected, str) else expected
            if not expected:
                continue
            chart = {'name': spec['name'], 'type': spec['kind'], 'expected_rows': expected}
            existing = next((c for c in self.state['charts'] if c['name'] == chart['name']), None)
            if existing:
                if existing['type'] != chart['type'] or existing['expected_rows'] != expected:
                    raise ValueError('resume_chart_identity_mismatch:' + chart['name'])
                continue
            created = self.api('chart-' + str(len(self.state['charts'])), 'POST',
                '/data-dashboards/' + self.state['dashboard_id'] + '/charts', {
                    'name': chart['name'], 'chart_type': chart['type'], 'sql_text': spec['sql'],
                    'chart_config': json.dumps(spec['config'], ensure_ascii=False),
                    'layout_config': json.dumps({'size': spec['size']}),
                    'refresh_interval': 'manual', 'alert_enabled': False, 'alert_config': ''})
            chart['id'] = created['ID']
            self.state['charts'].append(chart)
            self.save('chart_created:' + chart['name'])
        self.verify_charts()
        self.save('completed')
        return self.state
