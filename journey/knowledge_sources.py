"""Read complete, stable knowledge-source snapshots through the public product API."""
import hashlib
import json
from urllib.parse import urlencode

from .client import Failure, segment


def list_sources(client, model_id):
    items, page, total = [], 1, None
    while True:
        data = client.product('GET', f'/semantic-models/{model_id}/sources?' +
                              urlencode({'page': page, 'page_size': 100}), expected_code='OK')
        rows = data.get('items')
        if (not isinstance(rows, list) or type(data.get('total')) is not int or
                data['total'] < 0 or (total is not None and data['total'] != total) or
                any(not isinstance(row, dict) or row.get('model_id') != model_id or
                    not isinstance(row.get('row_id'), str) or not row['row_id'] for row in rows)):
            raise Failure('invalid_knowledge_source_page')
        total = data['total']
        items.extend(rows)
        if len({row['row_id'] for row in items}) != len(items) or len(items) > total:
            raise Failure('duplicate_knowledge_source')
        if len(items) == total:
            return items
        if not rows:
            raise Failure('incomplete_knowledge_source_inventory')
        page += 1


def read_document(client, model_id, source_id, *, chunk_ids=None):
    """Read a whole snapshot, or stop once every explicitly cited chunk is found."""
    wanted = None if chunk_ids is None else set(chunk_ids)
    if wanted is not None and not wanted:
        raise ValueError('empty_cited_chunk_selection')
    segments, cursors, cursor, first = [], set(), '', None
    while True:
        data = client.product('GET', f'/semantic-models/{model_id}/sources/{segment(source_id)}/document?' +
                              urlencode({'page_size': 100, 'cursor': cursor}), expected_code='OK')
        source, status, rows = data.get('source'), data.get('segment_status'), data.get('segments')
        if (not isinstance(source, dict) or source.get('model_id') != model_id or source.get('row_id') != source_id or
                not isinstance(status, dict) or type(status.get('total')) is not int or status['total'] < 0 or
                not isinstance(rows, list) or any(not isinstance(row, dict) or
                    not isinstance(row.get('segment_id'), str) or not row['segment_id'] or
                    not isinstance(row.get('content'), str) for row in rows)):
            raise Failure('invalid_knowledge_document_page')
        if first is None:
            first = data
        elif data.get('etag') != first.get('etag') or status['total'] != first['segment_status']['total']:
            raise Failure('knowledge_document_changed_during_read')
        segments.extend(rows)
        if len({row['segment_id'] for row in segments}) != len(segments):
            raise Failure('duplicate_knowledge_segment')
        if wanted is not None and wanted.issubset({row.get('chunk_id') or row['segment_id'] for row in segments}):
            first['segments'] = [row for row in segments if (row.get('chunk_id') or row['segment_id']) in wanted]
            first['segment_status'] = {**status, 'selection_complete': True}
            return first
        cursor = status.get('next_cursor', '')
        if not isinstance(cursor, str) or (cursor and (cursor in cursors or not rows)):
            raise Failure('invalid_knowledge_document_cursor')
        if not cursor:
            if len(segments) != status['total']:
                raise Failure('incomplete_knowledge_document')
            first['segments'] = segments
            first['segment_status'].pop('next_cursor', None)
            return first
        cursors.add(cursor)


def content_sha256(document):
    content = sorted((row['segment_id'], row['content'], row.get('enabled')) for row in document['segments'])
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
