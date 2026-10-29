"""Own two isolated databases and verify business effects independently of the Agent."""
import re
import time
import uuid

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
    def __init__(self, execution, scenario):
        self.execution, self.scenario = execution, scenario
        suffix = execution.report['run_id'].replace('-', '')
        self.databases = {kind: {'name': 'j2_' + kind + '_' + suffix, 'cleanup': 'pending'}
                          for kind in ('employees', 'sales')}
        self.ids = {name: str(uuid.uuid5(uuid.UUID(execution.report['run_id']), name))
                    for name in ('customer_a', 'customer_b', 'item_a', 'item_b', 'item_c', 'item_d', 'item_e', 'item_f')}
        self.employee_name = '林青'
        self.state = {'databases': self.databases, 'ids': self.ids}
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

    def setup(self, schema):
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
        if self.scenario != 'initialize':
            columns.extend(['english_name VARCHAR(120)', 'hiring_date DATE', 'is_active BOOLEAN'])
        self.sql('employees', 'CREATE TABLE employees (' + ','.join(columns) + ')')
        employees = []
        for eid, name, dept, title in [('0007', '林青', '销售部', '销售经理'), ('00018', '陈星', '工程部', '工程师'), ('00042', '周宁', '产品部', '产品经理')]:
            row = dict(employee_id=eid, name=name, github_id='fixture-' + eid, title=title, city='上海', department=dept,
                       division='华东', team='测试组', manager_id=None)
            if self.scenario != 'initialize':
                row.update(english_name='Fixture ' + eid, hiring_date='2020-01-01', is_active=1)
            employees.append(row)
        self.insert('employees', 'employees', employees)
        self.state['employees_before'] = self.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')
        if self.scenario != 'initialize':
            for statement in schema.decode().split(';'):
                if statement.strip():
                    self.sql('sales', statement.strip())
            fixed = {'created_at': '2026-01-01 00:00:00', 'updated_at': '2026-01-01 00:00:00', 'version': 1}
            self.insert('sales', 'customers', [dict(customer_uuid=self.ids['customer_a'], name='Atlas', **fixed),
                                               dict(customer_uuid=self.ids['customer_b'], name='Orion', **fixed)])
            items = []
            definitions = [('a','Atlas 联调','a','0007','in_progress','2000-01-01'),
                ('b','Atlas 报价','a','0007','pending','2000-01-01'), ('c','Orion 验收','b','00018','done','2000-01-01'),
                ('d','Orion 依赖','b','00018','blocked','2100-01-01'), ('e','Atlas 取消','a','00042','cancelled','2000-01-01'),
                ('f','Orion 逾期','b','00018','pending','2000-01-01')]
            for key, item, customer, owner, status, due in definitions:
                items.append(dict(item_id=self.ids['item_' + key], customer_uuid=self.ids['customer_' + customer],
                    owner_eid=owner, item=item, status=status, status_reason='等待环境' if status=='blocked' else None,
                    resume_status='in_progress' if status=='blocked' else None, next_followup_at=None,
                    completed_at='2026-01-01 00:00:00' if status=='done' else None, expected_end=due, notes='原始备注',
                    eng_owner_eid='00018', product_owner_eid='00042', **fixed))
            self.insert('sales', 'progress_items', items)
        self.state['ready'] = True
        self.save()
        return {'employees_table': 'default.' + self.databases['employees']['name'] + '.employees',
                'sales_database': self.databases['sales']['name']}

    def snapshot(self):
        tables = self.sql('sales', 'SHOW TABLES')
        names = {next(iter(row.values())) for row in tables}
        result = {'tables': sorted(names), 'employees': self.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')}
        for table, key in [('customers', 'customer_uuid'), ('progress_items', 'item_id'), ('progress_changes', 'change_id')]:
            result[table] = self.sql('sales', 'SELECT * FROM ' + identifier(table) + ' ORDER BY ' + identifier(key)) if table in names else []
        return result

    def update_employee(self):
        self.sql('employees', 'UPDATE employees SET name=' + literal('林青新版') + " WHERE employee_id='0007'")
        self.employee_name = '林青新版'
        self.state['employees_before'] = self.sql('employees', 'SELECT * FROM employees ORDER BY employee_id')
        self.save()

    def cleanup(self, dependencies_confirmed):
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = time.monotonic() + 90
        try:
            for kind in ('sales', 'employees'):
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
