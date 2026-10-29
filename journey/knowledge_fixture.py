"""Provision and remove one case-owned knowledge base for a real Agent test."""

import json
import re
import time
from urllib.parse import urlencode

from .catalog import fixture_path
from .client import Failure, error_record, segment


class KnowledgeFixtureUnavailable(Exception):
    """The knowledge fixture could not be made ready in QA."""


def valid_fixture_alias(alias):
    return isinstance(alias, str) and bool(re.fullmatch(r'[a-z][a-z0-9_]{0,15}', alias))


def prepare_knowledge_fixtures(execution, fixtures, variables):
    single = execution.case.get('knowledge_fixture')
    multiple = execution.case.get('knowledge_fixtures')
    if single is not None and multiple is not None:
        raise Failure('ambiguous_knowledge_fixtures')
    if multiple is not None and (not isinstance(multiple, dict) or not multiple or
            any(not valid_fixture_alias(alias) for alias in multiple)):
        raise Failure('invalid_knowledge_fixtures')
    definitions = {'default': single} if single is not None else (multiple or {})
    for alias, definition in definitions.items():
        execution.report['stage'] = 'knowledge_fixture'
        fixture = (KnowledgeFixture(execution, definition) if single is not None else
                   KnowledgeFixture(execution, definition, alias=alias))
        fixtures[alias] = fixture
        values = fixture.setup()
        variables.update(values if single is not None else {alias + '_' + key: value for key, value in values.items()})


def knowledge_selection_ids(fixtures, selection):
    if (not isinstance(selection, list) or not selection or
            any(not isinstance(alias, str) or alias not in fixtures for alias in selection) or
            len(set(selection)) != len(selection) or any(
                fixtures[alias].state.get('status') != 'ready' or
                type(fixtures[alias].state.get('model_id')) is not int or
                fixtures[alias].state['model_id'] <= 0 for alias in selection)):
        raise Failure('invalid_knowledge_selection')
    result = [str(fixtures[alias].state['model_id']) for alias in selection]
    if len(set(result)) != len(result):
        raise Failure('invalid_knowledge_selection')
    return result


class KnowledgeFixture:
    def __init__(self, execution, definition, *, alias=None):
        self.execution = execution
        if alias is not None and not valid_fixture_alias(alias):
            raise Failure('invalid_knowledge_fixture_alias')
        self.alias = alias
        if not isinstance(definition, dict) or definition.get('kind') not in {'document', 'table'}:
            raise Failure('invalid_knowledge_fixture')
        self.kind = definition['kind']
        expected_keys = ({'kind', 'path', 'marker'} if self.kind == 'document' else
                         {'kind', 'path', 'table_config', 'verification'})
        if set(definition) != expected_keys or not isinstance(definition.get('path'), str) or not definition['path']:
            raise Failure('invalid_knowledge_fixture')
        fixtures = [item for item in execution.case.get('fixtures', []) if item.get('path') == definition['path']]
        if len(fixtures) != 1:
            raise Failure('invalid_knowledge_fixture')
        self.path = fixture_path(execution.case, fixtures[0])
        self.marker = definition.get('marker')
        self.table_config = definition.get('table_config')
        self.verification = definition.get('verification')
        if self.kind == 'document':
            if not isinstance(self.marker, str) or not self.marker or self.marker not in self.path.read_text(encoding='utf-8'):
                raise Failure('knowledge_fixture_marker_missing')
        else:
            table = self.table_config.get('create_table') if isinstance(self.table_config, dict) else None
            verification = self.verification
            identifiers = [table.get('name') if isinstance(table, dict) else None]
            if isinstance(verification, dict):
                identifiers += [verification.get('sum_column'), verification.get('filter_column')]
            if (not isinstance(table, dict) or 'conn_file_ids' in self.table_config or
                    not isinstance(verification, dict) or set(verification) !=
                    {'sum_column', 'filter_column', 'filter_value', 'expected_rows', 'expected_sum'} or
                    any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value)
                        for value in identifiers) or
                    not isinstance(verification['filter_value'], str) or
                    not re.fullmatch(r'[A-Za-z0-9_-]+', verification['filter_value']) or
                    type(verification['expected_rows']) is not int or type(verification['expected_sum']) is not int):
                raise Failure('invalid_knowledge_table_fixture')
        name = 'journey2_kb_' + execution.report['run_id'].replace('-', '')[:20]
        self.state = {'name': name + ('_' + alias if alias else ''),
                      'fixture': definition['path'], 'kind': self.kind, 'status': 'initial', 'cleanup': 'pending'}
        self.create_started = False
        self.upload_started = False
        if alias is None:
            execution.report['knowledge_fixture'] = self.state
        else:
            execution.report.setdefault('knowledge_fixtures', {})[alias] = self.state
        self.save()

    def write(self, name, value):
        path = 'knowledge-fixtures/' + self.alias + '/' + name if self.alias else name
        self.execution.evidence.write(path, value)

    def save(self):
        self.write('knowledge-fixture.json', self.state)
        self.execution.checkpoint()

    def product(self, method, path, body=None):
        return self.execution.client.product(method, path, body, expected_code='OK')

    def setup(self):
        try:
            return self._setup()
        except Failure as error:
            self.state['status'] = 'unavailable'
            self.state['failure'] = error_record('knowledge_fixture', error)
            self.save()
            raise KnowledgeFixtureUnavailable(error.code) from None

    def _setup(self):
        listing = self.product('GET', '/semantic-models?' + urlencode({'page_size': 100, 'search': self.state['name']}))
        items = listing.get('items')
        if not isinstance(items, list) or listing.get('next_page_token'):
            raise Failure('knowledge_inventory_incomplete')
        if any(isinstance(item, dict) and item.get('name') == self.state['name'] for item in items):
            raise Failure('knowledge_fixture_name_exists')
        self.upload_started = True
        self.state['status'] = 'uploading'
        self.save()
        endpoint = '/semantic-models/local-files/upload' if self.kind == 'document' else '/connectors/file/upload'
        uploaded = self.execution.client.upload_multipart(endpoint, self.path)
        file_id = uploaded.get('file_id') if self.kind == 'document' else None
        if self.kind == 'table':
            ids = uploaded.get('conn_file_ids')
            file_id = ids[0] if isinstance(ids, list) and len(ids) == 1 else None
        if not isinstance(file_id, str) or not file_id:
            raise Failure('knowledge_file_id_missing')
        self.state.update({'file_id': file_id, 'status': 'creating'})
        self.create_started = True
        self.save()
        source_input = {'source_type': 'local_file', 'file_name': self.path.name, 'file_id': file_id}
        if self.kind == 'table':
            source_input.update({'upload_kind': 'structured',
                'table_config': json.dumps({**self.table_config, 'conn_file_ids': [file_id]})})
        created = self.product('POST', '/semantic-models/create-with-sources', {
            'name': self.state['name'], 'description': 'Synthetic Journey-2 knowledge fixture',
            'image_index_enabled': False, 'sources': [source_input]})
        self.write('knowledge-create.json', created)
        model = created.get('model')
        model_id = model.get('id') if isinstance(model, dict) else None
        domain = created.get('data_domain')
        database_id = domain.get('database_id') if isinstance(domain, dict) else None
        initial_sources = created.get('sources')
        if (type(model_id) is not int or model_id <= 0 or model.get('name') != self.state['name'] or
                not isinstance(initial_sources, list) or len(initial_sources) != 1 or
                not isinstance(initial_sources[0], dict) or not isinstance(initial_sources[0].get('row_id'), str) or
                not initial_sources[0]['row_id'] or
                (self.kind == 'document' and (model.get('files') or {}).get('file_ids') != [file_id]) or
                (self.kind == 'table' and (type(database_id) is not int or database_id <= 0))):
            raise Failure('knowledge_create_identity_mismatch')
        self.state.update({'model_id': model_id, 'source_id': initial_sources[0]['row_id'], 'status': 'processing'})
        if self.kind == 'table':
            self.state.update({'database_id': database_id, 'table_name': self.table_config['create_table']['name']})
        self.save()
        deadline = min(time.monotonic() + 120, self.execution.client.deadline - 120)
        driven = set()
        while time.monotonic() < deadline:
            sources = self.product('GET', '/semantic-models/' + str(model_id) + '/sources?page=1&page_size=100')
            jobs = self.product('GET', '/semantic-models/' + str(model_id) + '/source-jobs')
            self.write('knowledge-sources.json', sources)
            self.write('knowledge-jobs.json', jobs)
            items = sources.get('items')
            if (not isinstance(items, list) or sources.get('total') != 1 or len(items) != 1 or
                    not isinstance(items[0], dict) or items[0].get('model_id') != model_id or
                    items[0].get('row_id') != self.state['source_id'] or
                    (self.kind == 'document' and items[0].get('source_file_id') != file_id) or
                    items[0].get('source_type') != ('file' if self.kind == 'document' else 'table')):
                raise Failure('knowledge_source_identity_mismatch')
            source = items[0]
            self.state.update({'source_id': source['row_id'], 'source_status': source.get('ingest_status')})
            self.save()
            if source.get('ingest_status') == 'failed':
                raise Failure('knowledge_source_failed', details={'error': source.get('error')})
            if source.get('ingest_status') == 'succeeded':
                if self.kind == 'document':
                    document = self.product('GET', '/semantic-models/' + str(model_id) + '/sources/' +
                                            segment(source['row_id']) + '/document?page_size=100')
                    self.write('knowledge-document.json', document)
                    segments = document.get('segments')
                    if (not isinstance(segments, list) or not any(isinstance(item, dict) and
                            isinstance(item.get('content'), str) and self.marker in item['content'] for item in segments)):
                        raise Failure('knowledge_content_unavailable')
                else:
                    if (source.get('db_name') != self.state['name'] or source.get('table_name') != self.state['table_name'] or
                            type(source.get('kb_table_id')) is not int or source['kb_table_id'] <= 0):
                        raise Failure('knowledge_table_identity_mismatch')
                    self.state['table_id'] = source['kb_table_id']
                    self.verify_table()
                self.state['status'] = 'ready'
                self.save()
                return {'knowledge_name': self.state['name'], 'knowledge_id': str(model_id)}
            if not isinstance(jobs.get('items'), list) or type(jobs.get('reconcile_required')) is not bool:
                raise Failure('knowledge_job_status_invalid')
            # The product's source screen drives each newly observed pending state.
            fingerprint = json.dumps(jobs['items'], sort_keys=True, ensure_ascii=False)
            if jobs['reconcile_required'] and fingerprint not in driven:
                driven.add(fingerprint)
                self.product('POST', '/semantic-models/' + str(model_id) + '/source-jobs/reconcile')
            time.sleep(min(3, max(0, deadline - time.monotonic())))
        raise Failure('knowledge_source_not_ready')

    def verify_table(self):
        config = self.verification
        table = '`' + self.state['name'] + '`.`' + self.state['table_name'] + '`'
        queries = [
            ('row_count', f'SELECT COUNT(*) AS row_count FROM {table}', config['expected_rows']),
            ('total', f"SELECT SUM(`{config['sum_column']}`) AS total FROM {table} "
                      f"WHERE `{config['filter_column']}` = '{config['filter_value']}'", config['expected_sum']),
        ]
        for column, sql, expected in queries:
            result = self.product('POST', '/catalog/nl2sql/run_sql',
                                  {'database_id': self.state['database_id'], 'sql': sql})
            self.write('knowledge-table-' + column + '.json', result)
            rows = result.get('rows')
            if (result.get('columns') != [column] or not isinstance(rows, list) or len(rows) != 1 or
                    not isinstance(rows[0], list) or len(rows[0]) != 1 or type(rows[0][0]) not in {int, str} or
                    str(rows[0][0]) != str(expected)):
                raise Failure('knowledge_table_fixture_mismatch', details={'check': column})

    def cleanup(self, *, dependencies_confirmed=True):
        if not dependencies_confirmed:
            self.state['cleanup'] = 'retained'
            self.state['cleanup_reason'] = 'dependent_agent_or_task_unconfirmed'
            self.execution.report['harness_errors'].append(
                {'stage': 'cleanup_knowledge', 'reason': 'dependent_agent_or_task_unconfirmed'})
            self.save()
            return
        client = self.execution.client
        previous_deadline = client.deadline
        client.deadline = time.monotonic() + 100
        try:
            model_id = self.state.get('model_id')
            if model_id is None:
                if self.create_started or (self.upload_started and not self.state.get('file_id')):
                    raise Failure('knowledge_fixture_outcome_unknown')
                self.state['cleanup'] = 'not_created'
                if self.kind == 'table' and self.state.get('file_id'):
                    self.cleanup_connector_file()
                return
            model = self.product('GET', '/semantic-models/' + str(model_id))
            files = (model.get('files') or {}).get('file_ids')
            tables = model.get('tables')
            table_match = (tables in ([], None) or
                           (isinstance(tables, list) and len(tables) == 1 and isinstance(tables[0], dict) and
                            tables[0].get('db_name') == self.state['name'] and
                            tables[0].get('table_names') == [self.state.get('table_name')]))
            if (model.get('id') != model_id or model.get('name') != self.state['name'] or
                    (self.kind == 'document' and (files != [self.state['file_id']] or tables not in ([], None))) or
                    (self.kind == 'table' and (files not in ([], None) or not table_match))):
                raise Failure('knowledge_cleanup_identity_mismatch')
            sources = self.product('GET', '/semantic-models/' + str(model_id) + '/sources?page=1&page_size=100')
            items = sources.get('items')
            if (sources.get('total') != 1 or not isinstance(items, list) or len(items) != 1 or
                    not isinstance(items[0], dict) or items[0].get('row_id') != self.state.get('source_id') or
                    (self.kind == 'document' and items[0].get('source_file_id') != self.state['file_id']) or
                    (self.kind == 'table' and (items[0].get('table_name') not in (None, self.state['table_name']) or
                                               items[0].get('db_name') not in (None, self.state['name'])))):
                raise Failure('knowledge_cleanup_sources_mismatch')
            refs = self.product('GET', '/semantic-models/' + str(model_id) + '/referencing-agents?limit=100')
            if refs.get('items') != [] or refs.get('next_cursor'):
                raise Failure('knowledge_fixture_in_use')
            deleted = self.product('DELETE', '/semantic-models/' + str(model_id))
            if deleted.get('deleted') is not True:
                raise Failure('knowledge_delete_not_confirmed')
            try:
                self.product('GET', '/semantic-models/' + str(model_id))
            except Failure as error:
                if error.code != 'http_error' or error.http_status != 404:
                    raise
            else:
                raise Failure('knowledge_delete_not_confirmed')
            self.state['cleanup'] = 'deleted'
            if self.kind == 'table':
                self.cleanup_connector_file()
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_knowledge', error))
        finally:
            client.deadline = previous_deadline
            self.save()

    def cleanup_connector_file(self):
        file_id = self.state['file_id']
        response = self.execution.client.request('POST', '/connectors/file/delete', {'conn_file_id': file_id})
        if response.get('code') != 'OK':
            raise Failure('connector_file_delete_not_confirmed')
        try:
            self.execution.client.request('GET', '/connectors/file/stream?' + urlencode({'conn_file_id': file_id}),
                                          binary=True, max_bytes=1_000_000)
        except Failure as error:
            if error.code != 'http_error' or error.http_status != 404:
                raise
        else:
            raise Failure('connector_file_delete_not_confirmed')
        self.state['upload_cleanup'] = 'deleted'
