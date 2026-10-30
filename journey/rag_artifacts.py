"""Decode the product's RAG collection, including its zero-result nil slice."""


def retrieved_chunks(artifact):
    data = artifact.get('data')
    if not isinstance(data, dict):
        raise ValueError('invalid_rag_artifact_data')
    chunks = data.get('chunks')
    if chunks is None and type(data.get('row_count')) is int and data['row_count'] == 0:
        return []
    if not isinstance(chunks, list):
        raise ValueError('invalid_rag_chunk_collection')
    return chunks
