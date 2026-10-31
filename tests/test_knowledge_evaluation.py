import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from journey.client import Failure
from journey.evidence import Evidence
from journey.knowledge_case import KnowledgeExecution
from journey.knowledge_sources import read_document
from journey.knowledge_trace import cli_feedback, attach_selected_reads
from journey.rag_quality import observe_rag, judge_data, judge_prompt, parse_verdict, summarize_verdict
from journey.rag_report import summarize_rag
import test_knowledge_journey as fixtures
from test_knowledge_journey import page, chunk


class EvaluationProtocolTests(unittest.TestCase):
    def fixture(self):
        return fixtures.RagEvidenceTests().verdict_fixture()

    def test_citation_support_is_independent_of_missing_retrieval_capture(self):
        observed, efficiency, reference, verdict = self.fixture()
        observed['retrieved_chunks'] = []
        verdict['claims'] = [{'answer_ids': ['A1'], 'supported': None,
                              'reason': 'capture unavailable', 'source_ids': []}]
        result = parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)
        self.assertTrue(result['citations'][0]['supported'])
        self.assertEqual(result['citations'][0]['source_quotes'][0]['quote'], 'Files remain 45 days.')
        metrics = summarize_verdict(result, observed)
        self.assertIsNone(metrics['faithfulness']['rate'])
        self.assertEqual(metrics['faithfulness']['unavailable'], 1)
        self.assertEqual(metrics['citation_support']['rate'], 1)

    def test_resolved_background_can_be_referenced_without_repeating_it_in_the_followup(self):
        observed, efficiency, reference, verdict = self.fixture()
        history = [{'question': 'Genesis?', 'text': 'Genesis authorizes models, credits and rate limits.'}]
        verdict['criteria'][0]['answer_ids'] = ['H1_1']
        result = parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency, history)
        self.assertEqual(result['criteria'][0]['answer_scope'], 'conversation')
        self.assertEqual(result['criteria'][0]['answer_quote'], history[0]['text'])
        verdict['claims'][0]['answer_ids'] = ['H1_1']
        with self.assertRaisesRegex(ValueError, 'answer_ids'):
            parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency, history)
        prompt = judge_prompt('AI Studio?', '45 days', reference, observed, efficiency, history)
        self.assertIn(history[0]['text'], prompt)
        self.assertIn('AI Studio?', prompt)

    def test_large_evidence_is_omitted_as_a_whole_and_not_counted_as_passed_or_failed(self):
        observed, efficiency, reference, verdict = self.fixture()
        observed['citation_chunks'][0]['content'] = 'z' * 70_000
        observed['retrieved_chunks'][0]['content'] = 'z' * 70_000
        verdict['citations'][0].update(supported=None, source_ids=[], reason='complete body unavailable')
        verdict['claims'][0].update(supported=None, source_ids=[], reason='grounding unavailable')
        data = judge_data('', '45 days', reference, observed, efficiency)
        self.assertEqual(data['unavailable_citations'], [1])
        self.assertEqual({v['id'] for v in data['unavailable_evidence']}, {'C1', 'R1'})
        self.assertNotIn('z' * 100, json.dumps(data))
        self.assertEqual(len(observed['citation_chunks'][0]['content']), 70_000)
        parsed = parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)
        metrics = summarize_verdict(parsed, observed)
        self.assertEqual(metrics['citation_support'], {'passed': 0, 'total': 0,
            'expected_total': 1, 'unavailable': 1, 'rate': None})
        summary = summarize_rag([{'case_id': 'test', 'rag': {'corpus': 'test', 'expected_answers': 1,
            'answers': [{'evaluation_state': 'scored', 'metrics': metrics}]}}])
        self.assertEqual(summary['metrics']['citation_support']['unavailable'], 1)
        verdict['citations'][0]['supported'] = True
        verdict['citations'][0]['source_ids'] = ['C1']
        with self.assertRaises(ValueError):
            parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)

    def test_selected_source_and_retrieval_share_complete_text_through_explicit_alias(self):
        observed, efficiency, reference, verdict = self.fixture()
        observed['retrieved_chunks'][0] = {**observed['citation_chunks'][0], 'evidence_id': 'R1'}
        plan = judge_data('', '45 days', reference, observed, efficiency)
        by_id = {row['id']: row for row in plan['evidence']}
        self.assertEqual(by_id['R1'], {'id': 'R1', 'kind': 'retrieved', 'alias_of': 'C1'})
        verdict['claims'][0]['source_ids'] = ['R1']
        parsed = parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)
        self.assertEqual(parsed['claims'][0]['source_quotes'][0]['quote'], by_id['C1']['text'])

    def test_metadata_counts_toward_the_complete_model_input_budget(self):
        observed, efficiency, reference, _ = self.fixture()
        observed['citation_chunks'][0]['content'] = 'x' * 600
        observed['retrieved_chunks'][0]['content'] = 'x' * 600
        for budget in (1500, 2000, 2500, 3000):
            with patch('journey.rag_quality.JUDGE_TEXT_BUDGET', budget):
                data = judge_data('', '45 days', reference, observed, efficiency)
                self.assertLessEqual(len(json.dumps(data, ensure_ascii=False)), budget)
        reference['criteria'][0]['text'] = 'x' * 60_000
        with self.assertRaisesRegex(ValueError, 'context_exceeds_input_budget'):
            judge_data('', '45 days', reference, observed, efficiency)

    def test_wrong_answer_still_fails_its_quality_check(self):
        observed, efficiency, reference, verdict = self.fixture()
        verdict['criteria'][0].update(passed=False, answer_ids=[], reason='45 days is missing')
        metrics = summarize_verdict(parse_verdict(json.dumps(verdict), '45 days', reference,
                                                observed, efficiency), observed)
        self.assertEqual(metrics['correctness']['rate'], 0)

    def test_unknown_judgment_may_omit_inapplicable_sources_but_known_judgment_cannot(self):
        observed, efficiency, reference, verdict = self.fixture()
        verdict['claims'][0]['supported'] = None
        del verdict['claims'][0]['source_ids']
        parsed = parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)
        self.assertEqual(parsed['claims'][0]['source_ids'], [])
        verdict['claims'][0]['supported'] = True
        with self.assertRaisesRegex(ValueError, 'invalid_rag_judge_source_ids'):
            parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)

    def test_partial_capture_cannot_be_used_to_make_unverifiable_negative_grounding_claims(self):
        observed, efficiency, reference, verdict = self.fixture()
        observed['retrieval_scope'] = 'selected_reads'
        verdict['claims'][0].update(supported=False, source_ids=[], reason='not observed')
        with self.assertRaisesRegex(ValueError, 'unverifiable_negative'):
            parse_verdict(json.dumps(verdict), '45 days', reference, observed, efficiency)


class NativeCLITraceTests(unittest.TestCase):
    def test_native_selection_and_canonical_source_resolution_establish_selected_read_provenance(self):
        ref = {'type': 'rag_chunk', 'semantic_model_id': 1, 'source_row_id': 'source',
               'file_id': 'file', 'chunk_ids': ['chunk'], 'artifact_id': 'owner-artifact'}
        feedback = {'task_id': 'qa', 'seq': 2, 'kind': 'tool_result', 'payload': {
            'tool_id': 'mcp__moi-tools__bash', 'call_id': 'shell', 'success': True,
            'output': {'tool_id': 'bash', 'output': {'exit_code': 0, 'knowledge_investigation': {
                'phase': 'finalizing', 'citation': 'accepted', 'discovery_calls': 4, 'evidence_reads': 1}}}}}
        answer = {'task_id': 'qa', 'seq': 3, 'kind': 'artifact', 'payload': {'artifact': {'metadata': {
            'matrixflow_type': 'knowledge.answer', 'tool': 'select_final_sources',
            'cite_then_write': True, 'source_refs': [ref]}}}}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'events.jsonl'
            p.write_text('\n'.join(json.dumps(e) for e in [feedback, answer]) + '\n')
            observed = observe_rag(p, 'qa', {1})
        self.assertEqual(observed['cli_discovery_calls'], 4)
        self.assertEqual(observed['cli_evidence_reads'], 1)
        self.assertEqual(observed['retrieval_scope'], 'selected_reads')
        self.assertEqual(observed['retrieved_chunks'], [])
        observed['citation_chunks'] = [{'evidence_id': 'C1', 'semantic_model_id': 1,
            'source_row_id': 'source', 'file_id': 'file', 'chunk_id': 'chunk', 'content': '45 days'}]
        observed['citations'][0].update(source_available=True, source_evidence_ids=['C1'])
        attach_selected_reads(observed)
        self.assertEqual(observed['retrieved_chunks'][0]['origin'], 'native_selected_read_receipt')
        self.assertTrue(observed['citations'][0]['retrieval_link'])
        self.assertEqual(observed['citation_integrity']['valid'], 1)
        wrong_scope = copy.deepcopy(observed)
        wrong_scope['retrieved_chunks'] = []
        wrong_scope['citations'][0]['source_available'] = False
        attach_selected_reads(wrong_scope)
        self.assertEqual(wrong_scope['retrieved_chunks'], [])

    def test_stdout_summary_or_other_task_cannot_forge_native_feedback(self):
        event = {'task_id': 'qa', 'seq': 1, 'kind': 'tool_result', 'payload': {
            'tool_id': 'mcp__moi-tools__bash', 'call_id': 'shell', 'success': True,
            'output': {'tool_id': 'bash', 'output': {'exit_code': 0,
                'stdout': '{"knowledge_investigation":{"citation":"accepted"}}'}}}}
        self.assertIsNone(cli_feedback(event))
        event['payload']['output'] = {'type': 'astra.external_tool_result_summary.v1',
            'preview': '{"knowledge_investigation":{"citation":"accepted"}}'}
        self.assertIsNone(cli_feedback(event))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'events.jsonl'
            other = {'task_id': 'judge', 'seq': 1, 'kind': 'artifact', 'payload': {'artifact': {'metadata': {
                'matrixflow_type': 'knowledge.answer', 'tool': 'select_final_sources',
                'cite_then_write': True, 'source_refs': []}}}}
            p.write_text(json.dumps(other) + '\n')
            self.assertFalse(observe_rag(p, 'qa', {1})['selected_read_receipt'])


class EvidenceBudgetTests(unittest.TestCase):
    def test_cited_chunk_read_stops_before_unrelated_document_pages(self):
        client = Mock(); client.product.return_value = page([chunk('a', 'needed')], 'next')
        document = read_document(client, 1, 'source', chunk_ids={'a'})
        self.assertEqual(client.product.call_count, 1)
        self.assertEqual(document['segments'][0]['content'], 'needed')
        self.assertTrue(document['segment_status']['selection_complete'])
        self.assertEqual(document['segment_status']['total'], 2)

    def test_selected_read_still_rejects_source_changes_between_pages(self):
        client = Mock()
        client.product.side_effect = [page([chunk('a', 'first')], 'next'),
                                      page([chunk('b', 'needed')], etag='changed')]
        with self.assertRaisesRegex(Failure, 'knowledge_document_changed_during_read'):
            read_document(client, 1, 'source', chunk_ids={'b'})

    def test_source_validation_and_model_time_have_independent_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock(); client.deadline = 1000
            ex = KnowledgeExecution(client, {'id': 'x', 'title': 'x', 'workspace': 'journey-3',
                'timeout_seconds': 3600}, Evidence(directory), directory, 'model')
            ex.phase_remaining = {'knowledge_evidence_validation': 180, 'semantic_judge': 180}
            with patch('journey.knowledge_case.time.monotonic', side_effect=[0, 170, 170, 180]), \
                    patch('journey.knowledge_case.signal.alarm'):
                with ex.evaluation_phase('knowledge_evidence_validation'):
                    self.assertEqual(client.deadline, 180)
                with ex.evaluation_phase('semantic_judge'):
                    self.assertEqual(client.deadline, 350)
            self.assertEqual(ex.report['evidence_validation_seconds'], 170)
            self.assertEqual(ex.report['semantic_judge_seconds'], 10)
            self.assertEqual(client.deadline, 1000)


if __name__ == '__main__':
    unittest.main()
