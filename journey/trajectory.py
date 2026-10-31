"""Quantify repetition without confusing legitimate pagination or checks with wasted work."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .diagnostics import analyze_events
from .rag_artifacts import retrieved_chunks
from .knowledge_trace import cli_feedback


def task_trajectory(path, tasks, task_id):
    analysis = analyze_events(path, tasks, include_calls=True)
    calls = [call for call in analysis['calls'] if call['task_id'] == task_id]
    events = [json.loads(line) for line in Path(path).read_text().splitlines()]
    by_seq = {event['seq']: event for event in events if event.get('task_id') == task_id}
    native = {item['call_id']: item['state'] for event in events if event.get('task_id') == task_id
              for item in [cli_feedback(event)] if item is not None}
    signatures, repeated, timeline, seen_chunks, no_gain, max_no_gain = [], defaultdict(list), [], set(), 0, 0
    for call in calls:
        signature = json.dumps([call['tool'], call['arguments']], sort_keys=True, ensure_ascii=False)
        key = hashlib.sha256(signature.encode()).hexdigest()
        signatures.append(key)
        repeated[key].append(call['call_id'])
        source = [by_seq[seq] for seq in call['seqs']]
        timestamps = [datetime.fromisoformat(event['created_at'].replace('Z', '+00:00'))
                      for event in source if isinstance(event.get('created_at'), str)]
        seconds = (max(timestamps) - min(timestamps)).total_seconds() if len(timestamps) > 1 else None
        artifacts = [event.get('payload', {}).get('artifact', {}) for event in events
                     if event.get('task_id') == task_id and event.get('kind') == 'artifact' and
                     event.get('payload', {}).get('tool_id') == 'search_rag_chunks' and
                     event.get('payload', {}).get('call_id') == call['call_id']]
        keys = {(str(chunk.get('semantic_model_id')), chunk.get('source_row_id'), chunk.get('chunk_id'))
                for artifact in artifacts for chunk in retrieved_chunks(artifact)
                if isinstance(chunk, dict)}
        novel = len(keys - seen_chunks)
        if call['tool'] == 'search_rag_chunks':
            no_gain = no_gain + 1 if not novel else 0
            max_no_gain = max(max_no_gain, no_gain)
        seen_chunks.update(keys)
        timeline.append({'call_id': call['call_id'], 'tool': call['tool'], 'arguments': call['arguments'],
                         'outcome': call['outcome'], 'seqs': call['seqs'], 'round': call['round'],
                         'seconds': seconds, 'new_retrieved_chunks': None if call['tool'] in
                         {'bash', 'mcp__moi-tools__bash'} else novel, 'errors': call['errors'],
                         'knowledge_feedback': native.get(call['call_id'])})
    observed_retrievals = any(call['tool'] == 'search_rag_chunks' for call in calls)
    for state in native.values():
        search = state.get('last_search')
        if isinstance(search, dict):
            observed_retrievals = True
            count = search.get('consecutive_searches_without_new_chunks', 0)
            if type(count) is int and count >= 0:
                max_no_gain = max(max_no_gain, count)
    patterns = []
    for width in range(1, min(4, len(signatures) // 2) + 1):
        for start in range(len(signatures) - width * 2 + 1):
            if signatures[start:start + width] == signatures[start + width:start + width * 2]:
                patterns.append({'width': width, 'call_ids': [call['call_id'] for call in
                                                               calls[start:start + width * 2]]})
    return {'tool_calls': len(calls), 'tool_rounds': len({call['round'] for call in calls if call['round'] is not None}),
            'exact_repetitions': [ids for ids in repeated.values() if len(ids) > 1],
            'repeated_patterns': patterns, 'max_consecutive_retrievals_without_new_chunks': max_no_gain if observed_retrievals else None,
            'timeline': timeline,
            'note': '重复是观察信号，不自动判为低效；工具耗时由服务端 created_at 推算，受时间戳精度限制。'}
