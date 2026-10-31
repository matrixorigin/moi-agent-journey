"""Read native CLI controller feedback; shell stdout is never provenance."""
import copy


def cli_feedback(event):
    payload = event.get('payload', {})
    if (event.get('kind') != 'tool_result' or payload.get('tool_id') not in
            {'bash', 'mcp__moi-tools__bash'} or payload.get('success') is not True):
        return None
    envelope = payload.get('output')
    if not isinstance(envelope, dict) or envelope.get('tool_id') != 'bash':
        return None
    output = envelope.get('output')
    if not isinstance(output, dict) or output.get('exit_code') != 0:
        return None
    state = output.get('knowledge_investigation')
    if not isinstance(state, dict) or state.get('phase') not in {'exploring', 'finalizing'}:
        return None
    for field in ('discovery_calls', 'evidence_reads', 'selected_source_count'):
        if field in state and (type(state[field]) is not int or state[field] < 0):
            raise ValueError('invalid_cli_knowledge_counter')
    return {'task_id': event['task_id'], 'call_id': payload['call_id'], 'seq': event['seq'],
            'state': copy.deepcopy(state)}


def selected_read_receipt(artifact):
    # MOI's native knowledge.answer is emitted after select_final_sources has
    # checked current-task source provenance and read coverage. It attests to
    # selected evidence, not all discovery candidates or answer correctness.
    metadata = artifact.get('metadata', {})
    return (metadata.get('matrixflow_type') == 'knowledge.answer' and
            metadata.get('cite_then_write') is True and metadata.get('tool') == 'select_final_sources')


def attach_selected_reads(observed):
    """Resolve the native selection receipt with independently opened source text."""
    if not observed.get('selected_read_receipt'):
        return
    by_id = {chunk['evidence_id']: chunk for chunk in observed['citation_chunks']}
    for citation in observed['citations']:
        if not citation.get('source_available'):
            continue
        for key in citation['source_evidence_ids']:
            source = by_id[key]
            evidence_id = 'R' + str(len(observed['retrieved_chunks']) + 1)
            observed['retrieved_chunks'].append({**source, 'evidence_id': evidence_id,
                'artifact_id': citation['reference'].get('artifact_id'),
                'source_task_id': observed['task_id'], 'origin': 'native_selected_read_receipt'})
            citation['retrieved_evidence_ids'].append(evidence_id)
        citation['retrieval_link'] = bool(citation['retrieved_evidence_ids'])
        citation['integrity'] = citation['source_available'] and citation['retrieval_link']
    observed['citation_integrity']['valid'] = sum(c['integrity'] for c in observed['citations'])
