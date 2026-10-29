"""Open the actual cited PDF/table through the same source APIs as the UI."""
import hashlib
import json
from urllib.parse import urlencode

from .client import Failure, segment
from .builder_flow import ProductCaseFailure
from .conversation_checks import ConversationChecks


def check_citation_open(execution, conversation_id, task_id, session_id, checks, fixture):
    check = ConversationChecks(execution, conversation_id, session_id, checks)
    refs = []
    for line in (execution.evidence.root / 'events.jsonl').read_text().splitlines():
        event = json.loads(line)
        if event.get('task_id') != task_id or event.get('kind') != 'artifact':
            continue
        artifact = event.get('payload', {}).get('artifact', {})
        metadata = artifact.get('metadata', {})
        if metadata.get('matrixflow_type') == 'knowledge.answer':
            refs.extend(ref for ref in metadata.get('source_refs', []) if isinstance(ref, dict))
    def request(method, path, body=None):
        try:
            return fixture.product(method, path, body)
        except Failure as error:
            if error.code != 'product_error' and (error.code != 'http_error' or error.http_status in {None, 401, 403}):
                raise
            check.record('引用来源可打开且内容正确', False, 'citation_source_error:' + str(error.http_status))
            raise ProductCaseFailure('citation_source_unavailable') from None
    state = fixture.state
    if fixture.kind == 'document':
        matches = [ref for ref in refs if ref.get('type') == 'rag_chunk' and
            str(ref.get('semantic_model_id')) == str(state['model_id']) and ref.get('file_id') == state['file_id'] and
            ref.get('source_row_id') == state['source_id']]
    else:
        matches = [ref for ref in refs if ref.get('type') == 'sql_table' and ref.get('database') == state['name'] and
                   ref.get('table') == state['name'] + '.' + state['table_name']]
    check.record('打开引用前核对本任务引用和来源身份', bool(matches), 'citation_open_identity_missing')
    if not matches:
        check.record('引用来源可打开且内容正确', False, 'citation_open_identity_missing')
        return
    ref = matches[0]
    if fixture.kind == 'document':
        path = '/semantic-models/' + str(ref['semantic_model_id']) + '/sources/file/' + segment(ref['file_id']) + '/preview'
        try:
            content = execution.client.request('GET', path, binary=True)
        except Failure as error:
            if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                raise
            check.record('引用来源可打开且内容正确', False, 'citation_preview_http:' + str(error.http_status))
            return
        original = fixture.path.read_bytes()
        execution.evidence.write('citation-preview.json', {'task_id': task_id, 'reference': ref,
            'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
        ok = content.startswith(b'%PDF-') and hashlib.sha256(content).digest() == hashlib.sha256(original).digest()
    else:
        sources = request('GET', '/semantic-models/' + str(state['model_id']) + '/sources?' +
                                urlencode({'page_size':100}))
        matched = [item for item in sources.get('items', []) if item.get('row_id') == state['source_id'] and
                   item.get('db_name') == ref['database'] and item.get('table_name') == state['table_name'] and
                   item.get('kb_table_id') == state['table_id']]
        if len(matched) != 1:
            check.record('引用来源可打开且内容正确', False, 'citation_table_source_mismatch')
            return
        data = request('POST', '/catalog/table/data',
                             {'id': matched[0]['kb_table_id'], 'page':1, 'page_size':20})
        columns = [item.get('name') for item in data.get('columns', [])]
        rows = data.get('data', [])
        verification = fixture.verification
        ok = False
        if (len(rows) == verification['expected_rows'] and len(set(columns)) == len(columns) and
                verification['sum_column'] in columns and verification['filter_column'] in columns):
            amount, group = columns.index(verification['sum_column']), columns.index(verification['filter_column'])
            try:
                ok = sum(int(row[amount]) for row in rows if row[group] == verification['filter_value']) == verification['expected_sum']
            except (TypeError, ValueError, IndexError):
                pass
        execution.evidence.write('citation-table-preview.json', {'task_id':task_id,'reference':ref,'source':matched[0],'preview':data})
    check.record('引用来源可打开且内容正确', ok, 'citation_preview_content_mismatch')
