"""Own two isolated databases and verify business effects independently of the Agent."""
import re
import time

from .client import Failure, error_record


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value):
        raise Failure('native_fixture_identifier_invalid')
    return '`' + value + '`'


def literal(value):
    if value is None:
        return 'NULL'
    if type(value) is int:
        return str(value)
    if not isinstance(value, str):
        raise Failure('native_fixture_value_invalid')
    return "CONVERT(UNHEX('" + value.encode().hex() + "') USING utf8mb4)"


class NativeSkillData:
    def __init__(self, execution, *, business_database=True):
        self.execution = execution
        suffix = execution.report['run_id'].replace('-', '')
        self.databases = {kind: {'name': 'j2_' + kind + '_' + suffix, 'cleanup': 'pending'}
                          for kind in (('employees', 'sales') if business_database else ('employees',))}
        self.state = {'databases': self.databases}
        execution.report['native_data'] = self.state

    def catalog(self, path, body):
        return self.execution.client.product('POST', '/catalog/' + path, body, expected_code='OK')

    def save(self):
        self.execution.evidence.write('native-data.json', self.state)
        self.execution.checkpoint()

    def sql(self, kind, query):
        database = self.databases[kind]
        result = self.execution.client.product('POST', '/query/run',
            {'db_name': database['name'], 'query': query, 'limit': 200}, expected_code=200)
        self.execution.evidence.append('native-sql.jsonl', {'database': database['name'], 'query': query, 'response': result})
        if result.get('status') != 'success' or result.get('truncated'):
            raise Failure('native_fixture_sql_failed', details={'status': result.get('status')})
        columns = [column['name'] for column in result.get('columns', [])]
        rows = []
        for cells in result.get('result', []):
            if len(cells) != len(columns) or any(not isinstance(cell, dict) or type(cell.get('Valid')) is not bool for cell in cells):
                raise Failure('native_fixture_sql_result_invalid')
            rows.append({column: cell['String'] if cell['Valid'] else None for column, cell in zip(columns, cells)})
        return rows

    def verify_identity(self, kind):
        database = self.databases[kind]
        info = self.catalog('database/info', {'id': database['id']}).get('database')
        listing = self.catalog('database/list', {'id': database['catalog_id'], 'include_stats': False}).get('list')
        valid = (isinstance(info, dict) and info.get('id') == database['id'] and info.get('name') == database['name'] and
                 isinstance(listing, list) and len([item for item in listing if item.get('id') == database['id'] and
                     item.get('name') == database['name'] and not item.get('reserved')]) == 1)
        if not valid:
            raise Failure('native_database_identity_unverified')

    def insert(self, kind, table, rows):
        for row in rows:
            self.sql(kind, 'INSERT INTO ' + identifier(table) + ' (' + ','.join(identifier(key) for key in row) + ') VALUES (' +
                     ','.join(literal(value) for value in row.values()) + ')')

    def setup(self):
        catalogs = self.catalog('list', {'include_stats': False}).get('list', [])
        matches = [item for item in catalogs if item.get('name') == 'default']
        if len(matches) != 1:
            raise Failure('native_default_catalog_unavailable')
        for kind, database in self.databases.items():
            database['catalog_id'] = matches[0]['id']
            items = self.catalog('database/list', {'id': database['catalog_id'], 'include_stats': False}).get('list')
            if not isinstance(items, list) or any(item.get('name') == database['name'] for item in items):
                raise Failure('native_database_name_not_free')
            database['create_started'] = True
            self.save()
            result = self.catalog('database/create', {'catalog_id': database['catalog_id'], 'name': database['name'],
                'description': 'Case-owned ' + self.execution.case['id'] + ' ' + self.execution.report['run_id']})
            database['id'] = result['id']
            self.save()
            self.verify_identity(kind)
        columns = ['employee_id VARCHAR(64) PRIMARY KEY', 'name VARCHAR(64) NOT NULL', 'github_id VARCHAR(120)',
                   'title VARCHAR(64)', 'city VARCHAR(64)', 'department VARCHAR(128)', 'division VARCHAR(64)',
                   'team VARCHAR(64)', 'manager_id VARCHAR(64)']
        self.sql('employees', 'CREATE TABLE employees (' + ','.join(columns) + ')')
        employees = []
        for eid, name, dept, title in [('0007', '林青', '销售部', '销售经理'), ('00018', '陈星', '工程部', '工程师'), ('00042', '周宁', '产品部', '产品经理')]:
            row = dict(employee_id=eid, name=name, github_id='fixture-' + eid, title=title, city='上海', department=dept,
                       division='华东', team='测试组', manager_id=None)
            employees.append(row)
        self.insert('employees', 'employees', employees)
        self.state['employees_before'] = self.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')
        self.state['ready'] = True
        self.save()
        values = {'employees_table': 'default.' + self.databases['employees']['name'] + '.employees'}
        if 'sales' in self.databases:
            values['sales_database'] = self.databases['sales']['name']
        return values

    def snapshot(self):
        tables = self.sql('sales', 'SHOW TABLES')
        names = {next(iter(row.values())) for row in tables}
        result = {'tables': sorted(names), 'employees': self.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')}
        for table, key in [('customers', 'customer_uuid'), ('progress_items', 'item_id'), ('progress_changes', 'change_id')]:
            result[table] = self.sql('sales', 'SELECT * FROM ' + identifier(table) + ' ORDER BY ' + identifier(key)) if table in names else []
        return result

    def update_employee(self):
        self.sql('employees', 'UPDATE employees SET name=' + literal('林青新版') + " WHERE employee_id='0007'")
        self.state['employees_before'] = self.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')
        self.save()

    def cleanup(self, dependencies_confirmed):
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = time.monotonic() + 90
        try:
            for kind in reversed(self.databases):
                database = self.databases[kind]
                try:
                    if not dependencies_confirmed:
                        raise Failure('dependent_task_unconfirmed')
                    if 'id' not in database:
                        if database.get('create_started'):
                            raise Failure('native_database_create_outcome_unknown')
                        database['cleanup'] = 'not_created'
                        continue
                    self.verify_identity(kind)
                    if self.catalog('database/ref_list', {'id': database['id']}).get('list') != []:
                        raise Failure('native_database_has_external_references')
                    database['tables_before_delete'] = self.sql(kind, 'SHOW TABLES')
                    # This entire database was created by this case after proving its name absent.
                    deleted = client.request('POST', '/catalog/database/delete', {'id': database['id']})
                    if deleted.get('code') != 'OK':
                        raise Failure('native_database_delete_rejected')
                    items = self.catalog('database/list', {'id': database['catalog_id'], 'include_stats': False}).get('list')
                    if not isinstance(items, list) or any(item.get('id') == database['id'] or item.get('name') == database['name'] for item in items):
                        raise Failure('native_database_delete_unconfirmed')
                    database['cleanup'] = 'deleted'
                except Exception as error:
                    database['cleanup'] = 'incomplete'
                    self.execution.report['harness_errors'].append(error_record('cleanup_native_database', error, resource=kind))
                finally:
                    self.save()
        finally:
            client.deadline = deadline
