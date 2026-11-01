"""Verify retrieval and final citation against this case's indexed source."""

import re
from decimal import Decimal, InvalidOperation

from .rag_quality import observe_rag


def evaluate_knowledge(path, task_id, session_id, fixture, marker, *, document=None):
    model_id, file_id, source_id = fixture['model_id'], fixture['file_id'], fixture['source_id']
    if type(model_id) is not int or not all(isinstance(value, str) and value for value in (file_id, source_id, marker)):
        raise ValueError('invalid_knowledge_assertion')
    observed = observe_rag(path, task_id, {model_id})
    retrieved = {(chunk['artifact_id'], chunk['chunk_id']) for chunk in observed['retrieved_chunks'] if
        str(chunk.get('semantic_model_id')) == str(model_id) and chunk.get('file_id') == file_id and
        chunk.get('source_row_id') == source_id and marker in chunk['content']}
    cited = False
    native = False
    refs = [item['reference'] for item in observed['citations']]
    for ref in refs:
        if (ref.get('type') != 'rag_chunk' or str(ref.get('semantic_model_id')) != str(model_id) or
                ref.get('file_id') != file_id or ref.get('source_row_id') not in {None, source_id}):
            continue
        ids = [ref.get('chunk_id')] + (ref.get('chunk_ids') if isinstance(ref.get('chunk_ids'), list) else [])
        cited |= ref.get('source_row_id') == source_id and any(
            (ref.get('artifact_id'), key) in retrieved for key in ids)
        if observed['selected_read_receipt'] and document is not None:
            source = document.get('source', {})
            if (source.get('model_id') != model_id or source.get('row_id') != source_id or
                    source.get('kb_file_id') != file_id or source.get('effective_enabled') is not True):
                raise ValueError('knowledge_snapshot_identity_mismatch')
            chunks = {row.get('chunk_id') or row['segment_id']: row for row in document['segments']
                      if row.get('enabled') is True}
            wanted = {key for key in ids if isinstance(key, str) and key}
            native |= (isinstance(ref.get('artifact_id'), str) and bool(ref['artifact_id']) and
                       bool(wanted) and wanted.issubset(chunks) and
                       any(marker in chunks[key]['content'] for key in wanted))
    unknown = observed['retrieval_calls'] > 0 and not observed['retrieval_evidence_complete']
    if observed['selected_read_receipt'] and document is None:
        unknown = True
    blocked = 'knowledge_retrieval_evidence_unavailable' if not retrieved and not native and unknown else None
    return [
        {'session': session_id, 'rule': '实际检索到指定知识库材料', 'passed': bool(retrieved) or native,
         'detail': '' if retrieved or native else blocked or 'knowledge_source_not_retrieved'},
        {'session': session_id, 'rule': '最终引用指向实际检索的材料', 'passed': cited or native,
         'detail': '' if cited or native else blocked or 'knowledge_citation_mismatch'},
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


def evaluate_knowledge_table(path, task_id, session_id, fixture, verification, *, baseline=None):
    database, table = fixture['name'], fixture['table_name']
    observed = observe_rag(path, task_id, {fixture['model_id']} if 'model_id' in fixture else set())
    calls = set(observed['sql_calls'])
    results = [(payload.get('call_id'), payload) for payload in observed['sql_results']]
    citations = [item['reference'] for item in observed['citations']]
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
    if observed['selected_read_receipt']:
        for ref in citations:
            if (ref.get('type') == 'sql_table' and ref.get('database') == database and
                    ref.get('table') == database + '.' + table and
                    isinstance(ref.get('artifact_id'), str) and ref['artifact_id']):
                if baseline is None:
                    uncertain = True
                elif native_aggregate_query(ref.get('sql'), fixture, verification):
                    if (baseline.get('columns') != ['total'] or baseline.get('rows') not in
                            ([[verification['expected_sum']]], [[str(verification['expected_sum'])]])):
                        raise ValueError('knowledge_table_snapshot_mismatch')
                    verified.add(ref['artifact_id'])
                else:
                    uncertain = True
    cited = any(ref.get('type') == 'sql_table' and ref.get('database') == database and
                ref.get('table') == database + '.' + table and ref.get('artifact_id') in verified
                for ref in citations)
    if calls.difference(call_id for call_id, _ in results):
        uncertain = True
    blocked = 'knowledge_sql_evidence_unverifiable' if not verified and uncertain else None
    return [
        {'session': session_id, 'rule': '实际执行指定表的目标聚合查询', 'passed': bool(verified),
         'detail': '' if verified else blocked or 'knowledge_table_query_failed'},
        {'session': session_id, 'rule': '最终引用指向该 SQL 结果', 'passed': cited,
         'detail': '' if cited else blocked or 'knowledge_table_citation_mismatch'},
    ], blocked


def native_aggregate_query(sql, fixture, verification):
    """Recognize the two aggregate contracts supported by this basic fixture.

    Native selection attests execution of this exact query. Do not interpret
    stdout as a result, or accept extra filters/subqueries as equivalent SQL.
    The independent fixture check and final-answer assertion establish value
    correctness separately from this provenance check.
    """
    if not isinstance(sql, str):
        return False
    identifier = lambda value: r'(?:' + re.escape(value) + r'|`' + re.escape(value) + r'`)'
    table = r'(?:' + identifier(fixture['name']) + r'\s*\.\s*)?' + identifier(fixture['table_name'])
    amount, department = (identifier(verification[key]) for key in ('sum_column', 'filter_column'))
    total = r'SUM\s*\(\s*' + amount + r'\s*\)(?:\s+(?:AS\s+)?[A-Za-z_][A-Za-z0-9_]*)?'
    scalar = (r'SELECT\s+' + total + r'\s+FROM\s+' + table + r'\s+WHERE\s+' + department +
              r"\s*=\s*'(?-i:" + re.escape(verification['filter_value']) + ")'")
    grouped = (r'SELECT\s+' + department + r'\s*,\s*(?:COUNT\s*\(\s*\*\s*\)'
        r'(?:\s+(?:AS\s+)?[A-Za-z_][A-Za-z0-9_]*)?\s*,\s*)?' + total +
        r'\s+FROM\s+' + table + r'\s+GROUP\s+BY\s+' + department +
        r'(?:\s+ORDER\s+BY\s+' + department + r'(?:\s+(?:ASC|DESC))?)?')
    return any(re.fullmatch(pattern + r'\s*;?\s*', sql.strip(), re.I) for pattern in (scalar, grouped))
