"""Create and remove one case-owned ordinary workspace database and table."""

import re
import time
from collections import Counter

from .client import Failure, error_record


class DatabaseFixtureUnavailable(Exception):
    """The case's database could not be prepared in QA."""


class DatabaseFixture:
    def __init__(self, execution, definition):
        self.execution = execution
        if not isinstance(definition, dict) or set(definition) != {'table', 'columns', 'rows'}:
            raise Failure('invalid_database_fixture')
        self.table, self.columns, self.rows = definition['table'], definition['columns'], definition['rows']
        identifier = r'[a-z][a-z0-9_]{0,30}'
        if (not isinstance(self.table, str) or not re.fullmatch(identifier, self.table) or
                not isinstance(self.columns, list) or not self.columns or not isinstance(self.rows, list) or
                not self.rows or len(self.rows) > 100 or
                any(not isinstance(col, dict) or set(col) != {'name', 'type'} or
                    not isinstance(col['name'], str) or not re.fullmatch(identifier, col['name']) or
                    not isinstance(col['type'], str) or
                    not re.fullmatch(r'(?:INT|BIGINT|VARCHAR\([1-9][0-9]{0,2}\))', col['type'])
                    for col in self.columns) or
                len({col['name'] for col in self.columns}) != len(self.columns)):
            raise Failure('invalid_database_fixture')
        for row in self.rows:
            if not isinstance(row, list) or len(row) != len(self.columns):
                raise Failure('invalid_database_fixture_row')
            for value, column in zip(row, self.columns):
                if column['type'].startswith('VARCHAR'):
                    size = int(column['type'][8:-1])
                    if not isinstance(value, str) or len(value) > size or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
                        raise Failure('invalid_database_fixture_row')
                elif type(value) is not int or not -(2**31) <= value < 2**31:
                    raise Failure('invalid_database_fixture_row')
        self.state = {'name': 'journey2_db_' + execution.report['run_id'].replace('-', '')[:20],
                      'table_name': self.table, 'status': 'initial', 'cleanup': 'pending'}
        self.create_started = self.table_create_started = self.write_started = self.write_finished = False
        execution.report['database_fixture'] = self.state
        self.save()

    def save(self):
        self.execution.evidence.write('database-fixture.json', self.state)
        self.execution.checkpoint()

    def product(self, path, body, *, code='OK'):
        return self.execution.client.product('POST', path, body, expected_code=code)

    def verify_identity(self):
        database_id = self.state['database_id']
        info = self.product('/catalog/database/info', {'id': database_id}).get('database')
        listing = self.product('/catalog/database/list', {'id': self.state['catalog_id'], 'include_stats': False}).get('list')
        if (not isinstance(info, dict) or info.get('id') != database_id or info.get('name') != self.state['name'] or
                not isinstance(listing, list) or len([x for x in listing if isinstance(x, dict) and
                    x.get('id') == database_id and x.get('name') == self.state['name'] and not x.get('reserved')]) != 1):
            raise Failure('database_identity_mismatch')

    def verify_children(self, table_id):
        children = self.product('/catalog/database/children', {'id': self.state['database_id']}).get('list')
        if (not isinstance(children, list) or len(children) != 1 or not isinstance(children[0], dict) or
                children[0].get('id') != table_id or children[0].get('name') != self.table or
                children[0].get('type') != 'table'):
            raise Failure('database_children_mismatch')

    def setup(self):
        try:
            return self._setup()
        except Failure as error:
            self.state['status'] = 'unavailable'
            self.state['failure'] = error_record('database_fixture', error)
            self.save()
            raise DatabaseFixtureUnavailable(error.code) from None

    def _setup(self):
        catalogs = self.product('/catalog/list', {'include_stats': False}).get('list')
        matches = [x for x in catalogs if isinstance(x, dict) and x.get('name') == 'default'] if isinstance(catalogs, list) else []
        if len(matches) != 1 or type(matches[0].get('id')) is not int or matches[0]['id'] <= 0:
            raise Failure('database_catalog_unavailable')
        catalog_id = matches[0]['id']
        self.state['catalog_id'] = catalog_id
        listing = self.product('/catalog/database/list', {'id': catalog_id, 'include_stats': False}).get('list')
        if not isinstance(listing, list) or any(isinstance(x, dict) and x.get('name') == self.state['name'] for x in listing):
            raise Failure('database_name_exists_or_inventory_invalid')
        self.create_started = True
        self.state['status'] = 'creating_database'
        self.save()
        created = self.product('/catalog/database/create', {'catalog_id': catalog_id, 'name': self.state['name'],
                                                           'description': 'Synthetic Journey-2 QA fixture'})
        database_id = created.get('id')
        if type(database_id) is not int or database_id <= 0:
            raise Failure('database_id_missing')
        self.state.update({'database_id': database_id, 'status': 'database_created'})
        self.save()
        self.verify_identity()
        self.state['status'] = 'creating_table'
        self.table_create_started = True
        self.save()
        created_table = self.product('/catalog/table/create', {'database_id': database_id, 'name': self.table,
                                                               'columns': self.columns})
        table_id = created_table.get('id')
        if type(table_id) is not int or table_id <= 0:
            raise Failure('database_table_id_missing')
        self.state.update({'table_id': table_id, 'status': 'table_created'})
        self.save()
        self.verify_children(table_id)
        self.state['status'] = 'seeding'
        self.write_started = True
        self.save()
        names = ', '.join('`' + col['name'] + '`' for col in self.columns)
        def sql_value(value):
            return "'" + value + "'" if isinstance(value, str) else str(value)
        rows = ', '.join('(' + ', '.join(sql_value(value) for value in row) + ')' for row in self.rows)
        insert = self.product('/query/run', {'db_name': self.state['name'],
            'query': 'INSERT INTO `' + self.table + '` (' + names + ') VALUES ' + rows, 'limit': 100}, code=200)
        self.execution.evidence.write('database-insert.json', insert)
        self.write_finished = insert.get('status') in {'success', 'failed'}
        if insert.get('status') != 'success' or insert.get('rows_affected') != len(self.rows):
            raise Failure('database_insert_failed', details={'status': insert.get('status')})
        table = '`' + self.state['name'] + '`.`' + self.table + '`'
        readback = self.product('/catalog/nl2sql/run_sql', {'database_id': database_id,
                                                          'sql': 'SELECT ' + names + ' FROM ' + table})
        self.execution.evidence.write('database-readback.json', readback)
        actual = readback.get('rows')
        expected = Counter(tuple(str(value) for value in row) for row in self.rows)
        if (readback.get('columns') != [col['name'] for col in self.columns] or
                not isinstance(actual, list) or any(not isinstance(row, list) for row in actual) or
                Counter(tuple(str(value) for value in row) for row in actual) != expected):
            raise Failure('database_fixture_data_mismatch')
        self.state['status'] = 'ready'
        self.save()
        return {'database_name': self.state['name'], 'database_id': str(database_id), 'table_id': str(table_id)}

    def cleanup(self):
        client = self.execution.client
        previous_deadline = client.deadline
        client.deadline = time.monotonic() + 100
        try:
            database_id, table_id = self.state.get('database_id'), self.state.get('table_id')
            if database_id is None:
                if self.create_started:
                    raise Failure('database_create_outcome_unknown')
                self.state['cleanup'] = 'not_created'
                return
            if (self.table_create_started and table_id is None) or (self.write_started and not self.write_finished):
                raise Failure('database_mutation_outcome_unknown')
            self.verify_identity()
            children = self.product('/catalog/database/children', {'id': database_id}).get('list')
            refs = self.product('/catalog/database/ref_list', {'id': database_id}).get('list')
            if refs != [] or not isinstance(children, list):
                raise Failure('database_fixture_in_use')
            if table_id is not None:
                if (len(children) != 1 or not isinstance(children[0], dict) or children[0].get('id') != table_id or
                        children[0].get('name') != self.table or children[0].get('type') != 'table'):
                    raise Failure('database_cleanup_children_mismatch')
                if self.product('/catalog/table/ref_list', {'id': table_id}).get('list') != []:
                    raise Failure('database_fixture_in_use')
                deleted = client.request('POST', '/catalog/table/delete', {'id': table_id})
                if deleted.get('code') != 'OK' or self.product('/catalog/database/children', {'id': database_id}).get('list') != []:
                    raise Failure('database_table_delete_not_confirmed')
            elif children:
                raise Failure('database_cleanup_children_mismatch')
            deleted = client.request('POST', '/catalog/database/delete', {'id': database_id})
            if deleted.get('code') != 'OK':
                raise Failure('database_delete_not_confirmed')
            listing = self.product('/catalog/database/list', {'id': self.state['catalog_id'], 'include_stats': False}).get('list')
            if (not isinstance(listing, list) or any(isinstance(x, dict) and
                    (x.get('id') == database_id or x.get('name') == self.state['name']) for x in listing)):
                raise Failure('database_delete_not_confirmed')
            self.state['cleanup'] = 'deleted'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_database', error))
        finally:
            client.deadline = previous_deadline
            self.save()
