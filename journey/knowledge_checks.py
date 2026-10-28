"""Verify retrieval and final citation against this case's indexed source."""

import json
import re
from decimal import Decimal, InvalidOperation


def evaluate_knowledge(path, task_id, session_id, fixture, marker):
    model_id, file_id, source_id = fixture['model_id'], fixture['file_id'], fixture['source_id']
    if type(model_id) is not int or not all(isinstance(value, str) and value for value in (file_id, source_id, marker)):
        raise ValueError('invalid_knowledge_assertion')
    calls, results, artifacts, citations = set(), {}, [], []
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict):
            continue
        call_id = payload.get('call_id')
        if event.get('kind') == 'tool_call' and payload.get('tool_id') == 'search_rag_chunks' and isinstance(call_id, str):
            calls.add(call_id)
        elif event.get('kind') == 'tool_result' and payload.get('tool_id') == 'search_rag_chunks' and isinstance(call_id, str):
            results[call_id] = payload.get('success')
        elif event.get('kind') == 'artifact':
            artifact = payload.get('artifact')
            if not isinstance(artifact, dict):
                continue
            if payload.get('tool_id') == 'search_rag_chunks' and isinstance(call_id, str):
                artifacts.append((call_id, artifact))
            metadata = artifact.get('metadata')
            if isinstance(metadata, dict) and metadata.get('matrixflow_type') == 'knowledge.answer':
                refs = metadata.get('source_refs')
                if isinstance(refs, list):
                    citations.extend(ref for ref in refs if isinstance(ref, dict))
    retrieved = set()
    observed = False
    for call_id, artifact in artifacts:
        if call_id not in calls or results.get(call_id) is not True:
            continue
        data = artifact.get('data')
        chunks = data.get('chunks') if isinstance(data, dict) else None
        artifact_id = artifact.get('artifact_id')
        if not isinstance(chunks, list) or not isinstance(artifact_id, str) or not artifact_id:
            continue
        observed = True
        for chunk in chunks:
            if (isinstance(chunk, dict) and str(chunk.get('semantic_model_id')) == str(model_id) and
                    chunk.get('file_id') == file_id and chunk.get('source_row_id') == source_id and
                    isinstance(chunk.get('content'), str) and marker in chunk['content'] and
                    isinstance(chunk.get('chunk_id'), str) and chunk['chunk_id']):
                retrieved.add((artifact_id, chunk['chunk_id']))
    cited = any(str(ref.get('semantic_model_id')) == str(model_id) and ref.get('file_id') == file_id and
                ref.get('source_row_id') == source_id and ref.get('type') == 'rag_chunk' and
                any((ref.get('artifact_id'), chunk_id) in retrieved for chunk_id in
                    ([ref.get('chunk_id')] + (ref.get('chunk_ids') if isinstance(ref.get('chunk_ids'), list) else [])))
                for ref in citations)
    unknown = bool(calls) and (any(call not in results or results[call] is None for call in calls) or
                              (any(results.get(call) is True for call in calls) and not observed))
    blocked = 'knowledge_retrieval_evidence_unavailable' if not retrieved and unknown else None
    return [
        {'session': session_id, 'rule': '实际检索到指定知识库材料', 'passed': bool(retrieved),
         'detail': '' if retrieved else blocked or 'knowledge_source_not_retrieved'},
        {'session': session_id, 'rule': '最终引用指向实际检索的材料', 'passed': cited,
         'detail': '' if cited else blocked or 'knowledge_citation_mismatch'},
    ], blocked


def table_result_matches(output, verification):
    """Validate supported result shapes; return None when the evidence is ambiguous."""
    sql, rows, columns = output.get('sql'), output.get('rows'), output.get('columns')
    if (not isinstance(sql, str) or not isinstance(rows, list) or not isinstance(columns, list) or
            any(not isinstance(column, str) for column in columns) or
            len(set(column.casefold() for column in columns)) != len(columns) or
            any(not isinstance(row, list) or len(row) != len(columns) for row in rows) or
            output.get('truncated') is True):
        return None
    sum_column, filter_column, filter_value = (verification[key] for key in
                                                ('sum_column', 'filter_column', 'filter_value'))
    identifier = lambda name: r'(?:`?[\w]+`?\s*\.\s*)?`?' + re.escape(name) + r'`?'
    aggregate = re.search(r'\bsum\s*\(\s*' + identifier(sum_column) +
                          r'\s*\)(?:\s+as\s+`?([\w]+)`?)?', sql, re.I)
    if not aggregate:
        return None
    alias = aggregate.group(1) or 'sum(' + sum_column + ')'
    normalized = [re.sub(r'[\s`]', '', column).casefold() for column in columns]
    if normalized.count(alias.casefold()) != 1:
        return None
    sum_index = normalized.index(alias.casefold())
    group = re.search(r'\bgroup\s+by\s+' + identifier(filter_column) + r'(?!\w)', sql, re.I)
    where = re.search(r'\bwhere\b(?:(?!\b(?:group|order|limit|having)\b).)*?' + identifier(filter_column) +
                      r'\s*=\s*\'' + re.escape(filter_value) + r"'", sql, re.I | re.S)
    if group:
        if normalized.count(filter_column.casefold()) != 1:
            return None
        filter_index = normalized.index(filter_column.casefold())
        selected = [row for row in rows if row[filter_index] == filter_value]
    elif where and not re.search(r'\bor\b', sql, re.I):
        selected = rows
    else:
        return None
    if not selected:
        return False
    try:
        values = [Decimal(str(row[sum_index])) for row in selected]
        if any(not value.is_finite() for value in values):
            return None
        return sum(values) == Decimal(str(verification['expected_sum']))
    except (InvalidOperation, ValueError):
        return None


def evaluate_knowledge_table(path, task_id, session_id, fixture, verification):
    database, table = fixture['name'], fixture['table_name']
    calls, results, citations = set(), [], []
    for line in path.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload')
        if event.get('task_id') != task_id or not isinstance(payload, dict):
            continue
        call_id = payload.get('call_id')
        if event.get('kind') == 'tool_call' and payload.get('tool_id') == 'query_sql' and isinstance(call_id, str):
            calls.add(call_id)
        elif event.get('kind') == 'tool_result' and payload.get('tool_id') == 'query_sql' and isinstance(call_id, str):
            results.append((call_id, payload))
        elif event.get('kind') == 'artifact':
            artifact = payload.get('artifact')
            metadata = artifact.get('metadata') if isinstance(artifact, dict) else None
            if isinstance(metadata, dict) and metadata.get('matrixflow_type') == 'knowledge.answer':
                refs = metadata.get('source_refs')
                if isinstance(refs, list):
                    citations.extend(ref for ref in refs if isinstance(ref, dict))
    verified, uncertain = set(), False
    for call_id, payload in results:
        if call_id not in calls or payload.get('success') is not True:
            continue
        wrapper = payload.get('output')
        output = wrapper.get('output') if isinstance(wrapper, dict) else None
        if not isinstance(output, dict):
            uncertain = True
            continue
        tables = output.get('table_names')
        if not isinstance(tables, list):
            uncertain = True
            continue
        names = [str(item).replace('`', '').casefold() for item in tables]
        if output.get('db_name') != database or (database + '.' + table).casefold() not in names:
            continue
        matches = table_result_matches(output, verification)
        if matches is None:
            uncertain = True
            continue
        artifact_id = output.get('artifact_id')
        if matches and isinstance(artifact_id, str) and artifact_id:
            verified.add(artifact_id)
        elif matches:
            uncertain = True
    cited = any(ref.get('type') == 'sql_table' and ref.get('database') == database and
                ref.get('table') == database + '.' + table and ref.get('artifact_id') in verified
                for ref in citations)
    if calls.difference(call_id for call_id, _ in results):
        uncertain = True
    blocked = 'knowledge_sql_evidence_unverifiable' if not verified and uncertain else None
    return [
        {'session': session_id, 'rule': '实际查询指定表并得到正确统计结果', 'passed': bool(verified),
         'detail': '' if verified else blocked or 'knowledge_table_query_failed'},
        {'session': session_id, 'rule': '最终引用指向该 SQL 结果', 'passed': cited,
         'detail': '' if cited else blocked or 'knowledge_table_citation_mismatch'},
    ], blocked
