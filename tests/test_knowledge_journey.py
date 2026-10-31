import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from journey.catalog import ROOT, load_case
from journey.client import Failure
from journey.knowledge_sources import content_sha256, list_sources, read_document
from journey.knowledge_case import KnowledgeExecution
from journey.evidence import Evidence
from journey.report import aggregate, finish_case
from journey.rag_quality import evidence_text, judge_chunks, observe_rag, parse_verdict, summarize_verdict
from journey.rag_report import summarize_rag
from journey.rag_report import valid_rag_result
from journey.trajectory import task_trajectory


def page(rows, cursor='', etag='fixed'):
    return {'source': {'model_id': 1, 'row_id': 'source'}, 'etag': etag,
            'segment_status': {'total': 2, 'next_cursor': cursor}, 'segments': rows}


def chunk(identity, content):
    return {'segment_id': identity, 'chunk_id': identity, 'content': content, 'enabled': True}


class KnowledgeSourceTests(unittest.TestCase):
    def test_reference_drift_disabled_source_and_api_failure_are_explicitly_unscored(self):
        with tempfile.TemporaryDirectory() as directory:
            execution = KnowledgeExecution(Mock(), {'id': 'case', 'title': 'test', 'workspace': 'journey-3',
                'timeout_seconds': 60}, Evidence(directory), directory, 'model')
            execution.corpus = {'model_id': 1, 'source_etags': {'source': 'fixed'}}
            execution.bound_corpora = [execution.corpus]
            reference = {'verify_whole_corpus': True, 'evidence': []}
            for enabled, etag, expected in [(True, 'fixed', 'matched'), (True, 'changed', 'changed'),
                                            (False, 'fixed', 'changed')]:
                execution.client.product.return_value = {'source': {'model_id': 1, 'row_id': 'source',
                    'effective_enabled': enabled}, 'etag': etag}
                self.assertEqual(execution.check_reference(reference)['state'], expected)
            execution.client.product.side_effect = Failure('http_error', 503)
            result = execution.check_reference(reference)
            self.assertEqual(result['state'], 'unavailable')
            self.assertEqual(result['sources'][0]['error']['http_status'], 503)

    def test_reads_nested_cursor_to_completion_and_excludes_recall_counters_from_hash(self):
        client = Mock()
        client.product.side_effect = [page([chunk('a', 'first')], 'cursor-a'), page([chunk('b', 'second')])]
        document = read_document(client, 1, 'source')
        self.assertEqual([row['segment_id'] for row in document['segments']], ['a', 'b'])
        self.assertIn('cursor=cursor-a', client.product.call_args.args[1])
        digest = content_sha256(document)
        document['segments'][0]['recall_count'] = 999
        self.assertEqual(content_sha256(document), digest)
        document['segments'][0]['content'] = 'changed'
        self.assertNotEqual(content_sha256(document), digest)

    def test_rejects_changed_truncated_duplicate_or_wrong_source_pages(self):
        variants = [
            [page([chunk('a', 'a')], 'c'), page([chunk('b', 'b')], etag='changed')],
            [page([chunk('a', 'a')])],
            [page([chunk('a', 'a')], 'c'), page([chunk('a', 'a')])],
            [{**page([chunk('a', 'a'), chunk('b', 'b')]), 'source': {'model_id': 2, 'row_id': 'source'}}],
        ]
        for responses in variants:
            with self.subTest(responses=responses):
                client = Mock(); client.product.side_effect = responses
                with self.assertRaises(Failure):
                    read_document(client, 1, 'source')

    def test_source_listing_does_not_accept_early_empty_or_duplicate_pages(self):
        for second in ([], [{'model_id': 1, 'row_id': 'a'}]):
            client = Mock()
            client.product.side_effect = [{'items': [{'model_id': 1, 'row_id': 'a'}], 'total': 2},
                                          {'items': second, 'total': 2}]
            with self.assertRaises(Failure):
                list_sources(client, 1)


class RagEvidenceTests(unittest.TestCase):
    def events(self, path):
        records = []
        def event(seq, kind, payload, task='qa'):
            records.append({'task_id': task, 'seq': seq, 'kind': kind, 'payload': payload,
                            'created_at': f'2026-10-30T00:00:{seq:02}Z'})
        for offset, call in [(0, 'one'), (4, 'two'), (8, 'three')]:
            event(offset + 1, 'tool_call', {'tool_id': 'search_rag_chunks', 'call_id': call,
                                          'arguments': {'query': 'retention'}})
            event(offset + 2, 'tool_result', {'tool_id': 'search_rag_chunks', 'call_id': call, 'success': True})
            event(offset + 3, 'artifact', {'tool_id': 'search_rag_chunks', 'call_id': call,
                'artifact': {'artifact_id': 'retrieval-' + call, 'data': {'chunks': [
                    {'chunk_id': 'chunk', 'content': 'Ordinary files remain 45 days.', 'semantic_model_id': 1,
                     'file_id': 'file', 'source_row_id': 'source'}]}}})
        ref = {'type': 'rag_chunk', 'artifact_id': 'retrieval-one', 'chunk_id': 'chunk',
               'semantic_model_id': 1, 'source_row_id': 'source', 'file_id': 'file'}
        event(13, 'artifact', {'artifact': {'metadata': {'matrixflow_type': 'knowledge.answer', 'source_refs': [ref]}}})
        event(14, 'artifact', {'artifact': {'metadata': {'matrixflow_type': 'knowledge.answer',
                 'source_refs': [{**ref, 'source_row_id': 'other'}]}}}, task='previous')
        path.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
        return [{'task_id': 'qa', 'phase': 'qa:question', 'event_count': 10},
                {'task_id': 'previous', 'phase': 'judge:old', 'event_count': 1}]

    def test_citations_and_retrieval_are_task_local_and_repetition_is_evidence_not_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'; tasks = self.events(path)
            observed = observe_rag(path, 'qa', {1})
            self.assertEqual(len(observed['citations']), 1)
            self.assertEqual(observed['citations'][0]['retrieval_link'], True)
            self.assertEqual(observed['citations'][0]['reference']['source_row_id'], 'source')
            trajectory = task_trajectory(path, tasks, 'qa')
            self.assertEqual(trajectory['tool_calls'], 3)
            self.assertEqual(trajectory['exact_repetitions'], [['one', 'two', 'three']])
            self.assertEqual(trajectory['max_consecutive_retrievals_without_new_chunks'], 2)
            self.assertEqual(trajectory['timeline'][0]['seconds'], 1)
            self.assertNotIn('assessment', trajectory)

    def test_multiple_bound_bases_allow_related_citations_without_allowing_other_bases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'; self.events(path)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            artifact = copy.deepcopy(next(event for event in records if event['seq'] == 3))
            artifact['seq'] = 15
            artifact['payload']['artifact']['data']['chunks'][0].update(semantic_model_id=2, source_row_id='second')
            answer = copy.deepcopy(next(event for event in records if event['seq'] == 13))
            answer['seq'] = 16
            answer['payload']['artifact']['metadata']['source_refs'][0].update(semantic_model_id=2, source_row_id='second')
            records += [artifact, answer]
            path.write_text('\n'.join(json.dumps(record) for record in records)+'\n')
            allowed = observe_rag(path, 'qa', {1, 2})
            restricted = observe_rag(path, 'qa', {1})
            self.assertTrue(allowed['citations'][0]['retrieval_link'])
            self.assertEqual(allowed['out_of_scope_chunks'], 0)
            self.assertFalse(restricted['citations'][0]['retrieval_link'])
            self.assertEqual(restricted['out_of_scope_chunks'], 1)

    def test_zero_result_nil_chunks_are_empty_evidence_and_not_an_execution_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'; tasks = self.events(path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            rows[2]['payload']['artifact']['data'] = {'chunks': None, 'row_count': 0}
            path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            observed = observe_rag(path, 'qa', {1})
            self.assertEqual(len(observed['retrieved_chunks']), 2)
            self.assertEqual(observed['retrieval_successes'], 3)
            self.assertFalse(observed['citations'][0]['retrieval_link'])
            timeline = task_trajectory(path, tasks, 'qa')['timeline']
            self.assertEqual([call['new_retrieved_chunks'] for call in timeline], [0, 1, 0])
            rows[2]['payload']['artifact']['data']['row_count'] = 1
            path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            for operation in (lambda: observe_rag(path, 'qa', {1}), lambda: task_trajectory(path, tasks, 'qa')):
                with self.assertRaisesRegex(ValueError, 'invalid_rag_chunk_collection'):
                    operation()

    def verdict_fixture(self):
        observed = {'retrieved_chunks': [{'evidence_id': 'R1', 'content': 'Files remain 45 days.'}],
                    'citation_chunks': [{'evidence_id': 'C1', 'content': 'Files remain 45 days.'}],
                    'citations': [{'index': 1, 'integrity': True, 'source_evidence_ids': ['C1']}],
                    'citation_integrity': {'valid': 1, 'total': 1}}
        efficiency = {'timeline': [{'call_id': 'real-call'}]}
        reference = {'criteria': [{'id': 'C01', 'text': '45 days'}]}
        verdict = {'criteria': [{'id': 'C01', 'passed': True, 'reason': 'correct', 'answer_ids': ['A1']}],
                   'claims': [{'answer_ids': ['A1'], 'supported': True, 'reason': 'in retrieval',
                               'source_ids': ['R1']}],
                   'citations': [{'index': 1, 'supported': True, 'reason': 'supported', 'answer_ids': ['A1'],
                                  'source_ids': ['C1']}],
                   'efficiency': {'assessment': 'efficient', 'summary': 'direct lookup', 'findings': []}}
        return observed, efficiency, reference, verdict

    def test_judge_cannot_invent_quotes_claim_grounding_citations_or_detours(self):
        observed, efficiency, reference, valid = self.verdict_fixture()
        self.assertTrue(parse_verdict(json.dumps(valid), '45 days', reference, observed, efficiency)['criteria'][0]['passed'])
        mutations = [lambda v: v['criteria'][0].update(answer_ids=['invented']),
                     lambda v: v['criteria'].clear(),
                     lambda v: v['claims'][0].update(source_ids=['C1']),
                     lambda v: v['citations'][0].update(source_ids=['invented']),
                     lambda v: v['efficiency'].update(assessment='inefficient', findings=[
                         {'kind': 'detour', 'reason': 'wrong', 'avoidable': True, 'call_ids': ['invented']}])]
        for mutation in mutations:
            value = copy.deepcopy(valid); mutation(value)
            with self.assertRaises(ValueError):
                parse_verdict(json.dumps(value), '45 days', reference, observed, efficiency)

    def test_unobserved_retrieval_and_missing_answers_do_not_become_perfect_scores(self):
        observed, _, _, verdict = self.verdict_fixture()
        observed['retrieved_chunks'] = []
        metrics = summarize_verdict(verdict, observed)
        self.assertIsNone(metrics['faithfulness']['rate'])
        summary = summarize_rag([{'case_id': 'case', 'rag': {'corpus': 'test', 'expected_answers': 2,
            'answers': [{'phase': 'question', 'evaluation_state': 'unavailable'}]}}])
        self.assertEqual(summary['answers_missing'], 1)
        self.assertEqual(summary['evaluation_states'], {'unavailable': 1})
        self.assertIsNone(summary['metrics']['correctness']['rate'])
        self.assertFalse(valid_rag_result({'corpus': 'test', 'expected_answers': 1, 'answers': [{
            'session': 'q', 'phase': 'q', 'question': 'q', 'text': 'a', 'task_id': 't',
            'evaluation_state': 'unavailable',
            'metrics': {'correctness': {'passed': 1, 'total': 0, 'rate': 1}}}]}))

    def test_json_envelope_does_not_repair_or_extract_a_verdict_from_other_text(self):
        observed, efficiency, reference, verdict = self.verdict_fixture()
        raw = json.dumps(verdict)
        for text in (raw, '  ```json\n' + raw + '\n```\n', '```\n' + raw + '\n```'):
            self.assertTrue(parse_verdict(text, '45 days', reference, observed, efficiency)['criteria'][0]['passed'])
        for text in ('Result: ' + raw, '```json\n' + raw + '\n```\nextra',
                     '```python\n' + raw + '\n```', '```json\n' + raw + '\n```\n```json\n{}\n```'):
            with self.assertRaises(ValueError):
                parse_verdict(text, '45 days', reference, observed, efficiency)

    def test_canonical_quote_requires_actual_same_source_retrieval_not_just_source_availability(self):
        observed, efficiency, reference, verdict = self.verdict_fixture()
        identity = {'semantic_model_id': 1, 'source_row_id': 'source', 'file_id': 'file', 'chunk_id': 'chunk'}
        for evidence in observed['retrieved_chunks'] + observed['citation_chunks']:
            evidence.update(identity)
        verdict['claims'][0]['source_ids'] = ['C1']
        self.assertTrue(parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)['criteria'][0]['passed'])
        for field in identity:
            changed = copy.deepcopy(observed)
            changed['retrieved_chunks'][0][field] = 'other'
            with self.assertRaisesRegex(ValueError, 'claim_not_in_observed_evidence'):
                parse_verdict(json.dumps(verdict), '45 days', reference, changed, efficiency)
        observed['retrieved_chunks'][0]['content'] = 'different retrieved text'
        with self.assertRaisesRegex(ValueError, 'claim_not_in_observed_evidence'):
            parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)

    def test_verified_bindings_support_context_claims_but_cannot_support_citations(self):
        observed, efficiency, reference, verdict = self.verdict_fixture()
        observed['context_evidence'] = [{'evidence_id': 'B1', 'content': '45 days'}]
        verdict['claims'][0]['source_ids'] = ['B1']
        self.assertTrue(parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)['criteria'][0]['passed'])
        verdict['citations'][0]['source_ids'] = ['B1']
        with self.assertRaisesRegex(ValueError, 'quote_not_in_cited_source'):
            parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)

    def test_judge_view_deduplicates_only_identical_evidence_and_keeps_full_text(self):
        original = {'evidence_id': 'R1', 'semantic_model_id': 1, 'source_row_id': 's',
                    'file_id': 'f', 'chunk_id': 'c', 'content': 'complete evidence', 'score': .8}
        chunks = [original, {**original, 'evidence_id': 'R2', 'score': .9},
                  {**original, 'evidence_id': 'R3', 'content': 'changed evidence'},
                  {**original, 'evidence_id': 'R4', 'semantic_model_id': 2}]
        self.assertEqual(judge_chunks(chunks), [{'evidence_id': c['evidence_id'], 'content': evidence_text(c)}
                                               for c in (chunks[0], chunks[2], chunks[3])])
        self.assertIn('semantic_model_id', judge_chunks(chunks)[0]['content'])
        self.assertEqual(len(chunks), 4)
        self.assertIn('score', original)


class KnowledgeDatasetTests(unittest.TestCase):
    def test_explicit_target_verifies_every_bound_corpus_and_preserves_question_reference(self):
        case = load_case('J3-35')
        catalog = json.loads((ROOT / 'datasets/journey-3/corpora.json').read_text())
        case['knowledge_target'] = {'agent_id': 'all-knowledge', 'corpora': list(catalog)}
        client = Mock(); client.requests = 0
        client.product.return_value = {'id': 'all-knowledge', 'workspace_id': 'workspace', 'status': 'active',
            'binding': {'knowledge_base_refs': [{'id': str(value['model_id']), 'workspace_id': 'workspace',
                'status': 'active'} for value in catalog.values()]}}
        def sources(_client, mid):
            corpus = next(value for value in catalog.values() if value['model_id'] == mid)
            return [{'model_id': mid, 'row_id': sid} for sid in corpus['source_ids']]
        with tempfile.TemporaryDirectory() as directory:
            execution = KnowledgeExecution(client, case, Evidence(directory), directory, 'model')
            execution.report['rag'] = {}
            with patch('journey.knowledge_case.discover', return_value=('/workspaces/workspace',
                      {'agent_id': 'momo', 'agent_workspace_id': 'system'})), \
                    patch('journey.knowledge_case.list_sources', side_effect=sources), \
                    patch('journey.knowledge_case.start_version'):
                execution.setup()
            self.assertEqual(execution.allowed_model_ids, {10001, 10002, 10003, 20001})
            self.assertEqual(len(execution.sources), 307)
            self.assertTrue(execution.report['rag']['corpus_inventory_matches'])
            self.assertEqual(execution.corpus['model_id'], catalog['cedar']['model_id'])
            self.assertEqual(execution.selector['agent_id'], 'all-knowledge')

    def test_dataset_keeps_57_questions_context_and_fixed_bound_agents(self):
        config = json.loads((ROOT / 'suites.json').read_text())
        suite = next(s for s in config['journeys'] if s['workspace'] == 'journey-3')
        self.assertEqual(len(suite['cases']), 57)
        corpora = json.loads((ROOT / 'datasets/journey-3/corpora.json').read_text())
        cases = [load_case(identity) for identity in suite['cases']]
        self.assertEqual(sum(len(s['turns']) for c in cases for s in c['sessions']), 61)
        self.assertEqual(len({c['rag']['key'] for c in cases}), 57)
        for case in cases:
            self.assertEqual(case['executor'], 'knowledge')
            self.assertIn(case['rag']['corpus'], corpora)
            for session in case['sessions']:
                self.assertEqual(session['delivery_kind'], 'answer')
                for turn in session['turns']:
                    if 'rag_reference' in turn:
                        ref = turn['rag_reference']
                        self.assertEqual(ref['status'], 'reviewed')
                        self.assertTrue(ref['criteria'])
                        self.assertTrue(all(item['source_id'] in corpora[case['rag']['corpus']]['source_ids']
                                            for item in ref['evidence']))

    def test_question_and_judge_use_separate_agents_and_only_judge_sees_reference(self):
        case = load_case('J3-30')
        reference = {'status': 'reviewed', 'citation_required': False, 'evidence': [],
                     'criteria': [{'id': 'C01', 'text': 'SCORER-ONLY-GOLDEN'}]}
        case['sessions'] = [{'id': 'qa', 'delivery_kind': 'answer', 'expected_checks': 3,
                             'turns': [{'phase': 'question', 'prompt': 'How long?', 'rag_reference': reference}]}]
        corpus = json.loads((ROOT / 'datasets/journey-3/corpora.json').read_text())['cedar']
        client = Mock(); client.requests = 0
        def product(method, path, body=None, **kwargs):
            if method == 'GET':
                return {'id': corpus['agent_id'], 'workspace_id': 'workspace', 'status': 'active',
                        'binding': {'knowledge_base_refs': [{'id': str(corpus['model_id']),
                            'workspace_id': 'workspace', 'status': 'active'}]}}
            if method == 'POST':
                return {'id': body['id']}
            raise AssertionError((method, path))
        client.product.side_effect = product
        sent = []
        def send(execution, cid, phase, prompt):
            selector = execution.conversation_selector(cid)
            sent.append((phase, selector, prompt))
            task_id = 'task-' + str(len(sent))
            entry = {'task_id': task_id, 'conversation_id': cid, 'phase': phase, 'state': 'completed',
                     'trace_id': 'trace-' + task_id, 'seconds': 1.0, 'event_count': 0, 'capture_complete': True}
            execution.tasks.append(entry)
            text = '45 days'
            if phase.startswith('judge:'):
                text = json.dumps({'criteria': [{'id': 'C01', 'passed': False, 'reason': 'incorrect', 'answer_ids': []}],
                    'claims': [], 'citations': [],
                    'efficiency': {'assessment': 'efficient', 'summary': 'direct', 'findings': []}})
            return entry, {'status': {'state': 'completed', 'message': {'role': 'agent',
                             'parts': [{'kind': 'text', 'text': text}]}}}
        with tempfile.TemporaryDirectory() as directory:
            evidence = Evidence(Path(directory) / 'results/J3-30')
            (evidence.root / 'events.jsonl').write_text('')
            execution = KnowledgeExecution(client, case, evidence, directory, 'model')
            with patch('journey.knowledge_case.discover', return_value=('/workspaces/workspace',
                        {'agent_id': 'momo', 'agent_workspace_id': 'system'})), \
                    patch('journey.knowledge_case.list_sources', return_value=[{'row_id': sid, 'model_id': corpus['model_id']}
                                                                              for sid in corpus['source_ids']]), \
                    patch('journey.knowledge_case.start_version'), patch('journey.knowledge_case.finish_version'), \
                    patch('journey.knowledge_case.signal.alarm'), \
                    patch.object(KnowledgeExecution, 'send', send), patch.object(KnowledgeExecution, 'cleanup'):
                result = execution.run()
                original_sent = list(sent)
                original_tasks = copy.deepcopy([task for task in result['tasks'] if not task['phase'].startswith('judge:')])
                prior = copy.deepcopy(result)
                prior['harness_errors'] = [{'stage': 'semantic_judge', 'reason': 'invalid_json'},
                                          {'stage': 'trajectory_capture', 'reason': 'old_decode_failure'}]
                sent.clear()
                replay = KnowledgeExecution(client, case, evidence, directory, 'model').reevaluate(prior)
                self.assertEqual(len(sent), 1)
                self.assertTrue(sent[0][0].startswith('judge:'))
                self.assertEqual([task for task in replay['tasks'] if not task['phase'].startswith('judge:')], original_tasks)
                self.assertEqual(replay['task_seconds'], result['task_seconds'])
                self.assertEqual(replay['harness_errors'], [])
                self.assertEqual([item['task_id'] for item in replay['rag']['trajectories']],
                                 [task['task_id'] for task in original_tasks])
                self.assertEqual(replay['evaluation_replays'][0]['previous_harness_errors'], prior['harness_errors'])
                self.assertEqual(prior['harness_errors'][0]['reason'], 'invalid_json')
                sent.clear()
                sent.extend(original_sent)
                evidence.write('result.json', result)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['harness_errors'], [])
            self.assertEqual(sent[0][1]['agent_id'], corpus['agent_id'])
            self.assertNotIn('SCORER-ONLY-GOLDEN', sent[0][2])
            self.assertEqual(sent[1][1]['agent_id'], 'momo')
            self.assertIn('SCORER-ONLY-GOLDEN', sent[1][2])
            with patch('journey.report.load_case', return_value=case):
                finalized = finish_case('J3-30', Path(directory) / 'results')
            self.assertEqual(finalized['test_status'], 'completed')
            self.assertEqual(finalized['task']['state'], 'completed')
            self.assertEqual(finalized['rag']['answers'][0]['metrics']['correctness']['rate'], 0)
            markdown = (evidence.root / 'case-summary.md').read_text()
            self.assertIn('**效率**', markdown)
            self.assertIn('**正确性**', markdown)
            self.assertIn('45 days', markdown)

    def test_missing_run_reports_every_expected_answer_without_claiming_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = aggregate(root / 'source', root / 'bundle', 'journey-3')
            self.assertEqual(report['total'], 57)
            self.assertEqual(report['rag']['answers_expected'], 58)
            self.assertEqual(report['rag']['answers_missing'], 58)
            self.assertIsNone(report['rag']['metrics']['correctness']['rate'])
            markdown = (root / 'bundle/summary.md').read_text()
            self.assertIn('### 效率', markdown)
            self.assertIn('### 正确性', markdown)

    def test_explicit_subset_keeps_missing_selected_answers_in_the_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = aggregate(root / 'source', root / 'bundle', 'journey-3', ['J3-01', 'J3-35'])
            self.assertEqual(report['total'], 2)
            self.assertEqual(report['rag']['answers_expected'], 2)
            self.assertEqual(report['rag']['answers_missing'], 2)
            self.assertEqual(report['test_errors'], 2)
            self.assertEqual(report['case_selection'], ['J3-01', 'J3-35'])
            self.assertIn('仅统计显式选择的题目', (root / 'bundle/summary.md').read_text())
            for selection in ([], ['J3-01', 'J3-01'], ['J2-01'], ['J3-999']):
                with self.assertRaisesRegex(ValueError, 'invalid_case_selection'):
                    aggregate(root / 'source', root / 'invalid', 'journey-3', selection)


if __name__ == '__main__':
    unittest.main()
