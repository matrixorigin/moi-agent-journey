"""Use the product's artifact resolver and volume selection to populate a KB."""
import json
import time
from urllib.parse import urlencode

from .artifact_checks import find_task_artifact
from .builder_flow import ProductCaseFailure
from .client import Failure, error_record
from .knowledge_fixture import KnowledgeFixture
from .native_skill_cleanup import cleanup_artifacts


class ArtifactKnowledgeFixture(KnowledgeFixture):
    def __init__(self, execution, cid, task_id, filename, marker, checks, session):
        self.path = find_task_artifact(execution, cid, task_id, filename, checks, session)
        if self.path is None:
            raise ProductCaseFailure('knowledge_artifact_missing')
        items = json.loads((execution.evidence.root / 'artifacts' / cid / 'inventory.json').read_text())
        matches = [item for item in items if item.get('task_id') == task_id and item.get('file_name') == filename]
        item = matches[0]
        selector = execution.conversation_selector(cid)
        if (item.get('conversation_id') != cid or item.get('workspace_id') != execution.root.rsplit('/', 1)[-1] or
                any(item.get(key) != selector[key] for key in ('agent_id', 'agent_workspace_id')) or
                not isinstance(item.get('artifact_id'), str) or type(item.get('version')) is not int):
            raise Failure('knowledge_artifact_identity_mismatch')
        self.artifact = {key: item[key] for key in ('artifact_id', 'version')}
        self.kind, self.marker = 'document', marker
        self.table_config, self.verification = None, None
        self.register(execution, 'generated')
        self.state['conversation_id'] = cid

    def _setup(self):
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
        try:
            content = '\n'.join(page.extract_text() or '' for page in PdfReader(self.path).pages)
        except PdfReadError:
            raise Failure('knowledge_artifact_unreadable') from None
        if self.marker not in content:
            raise Failure('knowledge_artifact_content_mismatch')
        resolved = self.execution.client.product('POST', self.execution.root + '/agent-artifacts/resolve',
                                                 {'items': [self.artifact]})
        self.write('artifact-resolve.json', resolved)
        items = resolved.get('items')
        item = items[0] if isinstance(items, list) and len(items) == 1 else {}
        if (resolved.get('failed') not in (None, []) or
                any(item.get(key) != value for key, value in self.artifact.items()) or
                not isinstance(item.get('file_id'), str) or not item['file_id'] or
                type(item.get('volume_id')) is not int or item['volume_id'] <= 0):
            raise Failure('artifact_resolution_mismatch')
        listing = self.product('GET', '/semantic-models?' + urlencode({'page_size': 100, 'search': self.state['name']}))
        if (not isinstance(listing.get('items'), list) or listing.get('next_page_token') or
                any(row.get('name') == self.state['name'] for row in listing['items'])):
            raise Failure('knowledge_fixture_name_unconfirmed')
        self.create_started = True
        self.state.update({'file_id': item['file_id'], 'artifact': self.artifact})
        self.save()
        created = self.product('POST', '/semantic-models/create-empty', {
            'name': self.state['name'], 'description': 'Synthetic generated artifact regression',
            'image_index_enabled': False})
        self.write('knowledge-create.json', created)
        model = created.get('model') or {}
        if type(model.get('id')) is not int or model['id'] <= 0 or model.get('name') != self.state['name']:
            raise Failure('knowledge_create_identity_mismatch')
        self.state['model_id'] = model['id']
        self.save()
        appended = self.product('POST', '/semantic-models/' + str(model['id']) + '/sources', {
            'sources': [], 'source_selections': [{'kind': 'volume_files', 'volume_id': item['volume_id'],
                'all_selected': False, 'selected_file_ids': [item['file_id']]}]})
        self.write('knowledge-append.json', appended)
        self.reconcile_source_identity()
        if not self.state.get('source_id'):
            raise Failure('artifact_not_added_to_knowledge')
        return self.wait_ready()

    def reconcile_source_identity(self):
        sources = self.product('GET', '/semantic-models/' + str(self.state['model_id']) + '/sources?page=1&page_size=100')
        items = sources.get('items')
        if items == [] and sources.get('total') == 0:
            return
        if (not isinstance(items, list) or len(items) != 1 or sources.get('total') != 1 or
                items[0].get('model_id') != self.state['model_id'] or
                items[0].get('source_file_id') != self.state['file_id'] or
                not isinstance(items[0].get('row_id'), str) or not items[0]['row_id']):
            raise Failure('knowledge_source_identity_mismatch')
        self.state['source_id'] = items[0]['row_id']
        self.save()

    def cleanup(self, *, dependencies_confirmed=True):
        # An append can fail after the empty KB was created; retain its exact ownership.
        if dependencies_confirmed and self.state.get('model_id') and not self.state.get('source_id'):
            deadline = self.execution.client.deadline
            self.execution.client.deadline = time.monotonic() + 30
            try:
                self.reconcile_source_identity()
            except Failure as error:
                self.state['cleanup'] = 'incomplete'
                self.execution.report['harness_errors'].append(error_record('cleanup_knowledge', error))
                self.save()
                return
            finally:
                self.execution.client.deadline = deadline
        super().cleanup(dependencies_confirmed=dependencies_confirmed)
        if (not dependencies_confirmed or self.state.get('cleanup') not in {'deleted', 'not_created'} or
                self.state.get('artifacts_cleanup') in {'deleted', 'incomplete'}):
            return
        client, deadline = self.execution.client, self.execution.client.deadline
        client.deadline = time.monotonic() + 30
        try:
            cleanup_artifacts(self, self.state['conversation_id'])
            self.state['artifacts_cleanup'] = 'deleted'
        except Exception as error:
            self.state['artifacts_cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_artifact_knowledge', error))
        finally:
            client.deadline = deadline
            self.save()
