import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from journey.benchmark_history import FIELDS, export, metrics_row, write_metrics
from journey.benchmark_metrics import public_result
from journey.benchmark_store import Store, canonical, digest
from journey.catalog import load_case
from journey.client import Failure
from journey.evidence import Evidence
from journey.knowledge_case import KnowledgeExecution
from journey.ops_knowledge_case import BenchmarkDependencyUnavailable, OpsKnowledgeExecution, validate_sql_citations
from journey.rag_quality import citation_integrity_metric
from journey.rag_report import valid_rag_result
from journey.report import _missing_case, finish_case


class OpsKnowledgeTests(unittest.TestCase):
    def test_reads_of_benchmark_archives_cannot_produce_a_quality_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            execution = OpsKnowledgeExecution.__new__(OpsKnowledgeExecution)
            execution.evidence = Evidence(tmp)
            execution.client = Mock()
            execution.root = '/workspaces/fixture'
            execution.allowed_model_ids = {5}
            execution.tasks = []
            execution.report = {'benchmark': {'state': 'eligible'}}
            execution.case = {}
            file_id = '128024a3-5333-4130-8760-9bab33b272c1'
            (Path(tmp) / 'events.jsonl').write_text(canonical({'task_id': 'task', 'kind': 'tool_call',
                'payload': {'tool_id': 'read_file', 'arguments': {'file_id': file_id}}}) + '\n')
            answer = {'task_id': 'task', 'seconds': 1}
            with patch('journey.ops_knowledge_case.observe_rag', return_value={}), \
                    patch('journey.ops_knowledge_case.task_trajectory', return_value={}), \
                    patch('journey.benchmark_store.Store.accessed_private_evidence', return_value=True) as lookup:
                self.assertIsNone(execution.prepare_answer({}, {}, answer))
            self.assertIn(file_id, lookup.call_args.args[0])
            self.assertEqual(answer['evaluation_state'], 'unavailable')
            self.assertEqual(execution.report['benchmark']['state'], 'blocked')

    def test_declared_but_unbound_or_unexpected_sources_block_scoring(self):
        valid = {'db_name': 'fixture', 'table_name': 'rows', 'effective_enabled': True, 'ingest_status': 'succeeded'}
        for sources in ([{**valid, 'effective_enabled': False, 'governance_status': 'legacy_unbound'}],
                        [valid, {**valid, 'table_name': 'unexpected'}]):
            with self.subTest(sources=sources), tempfile.TemporaryDirectory() as tmp:
                case = load_case('J3-88')
                case.update(ops_target={'agent_id': 'clone', 'model_ids': [5], 'tables': ['fixture.rows'],
                    'expected_enabled_tables': {'5': ['fixture.rows']}}, snapshot_id='s1', snapshot_manifest_sha256='expected')
                client = Mock(requests=0)
                client.product.return_value = {'id': 'clone', 'name': 'Private fixture', 'workspace_id': 'ws', 'status': 'active',
                    'binding': {'knowledge_base_refs': [{'id': '5', 'workspace_id': 'ws'}]}}
                execution = OpsKnowledgeExecution(client, case, Evidence(tmp), tmp, 'model')
                execution.report['rag'] = {}
                with patch('journey.ops_knowledge_case.discover_workspace', return_value='/workspaces/ws'), \
                        patch('journey.benchmark_store.Store.dataset', return_value={'state': 'complete', 'manifest_sha256': 'expected'}), \
                        patch('journey.ops_knowledge_case.list_sources', return_value=sources), \
                        self.assertRaisesRegex(BenchmarkDependencyUnavailable, 'benchmark_knowledge_sources_not_ready'):
                    execution.setup()
                self.assertEqual(execution.report['benchmark']['state'], 'blocked')
                self.assertFalse(execution.report['rag']['corpus_inventory_matches'])
                client.stream_rpc.assert_not_called()

    def test_malformed_or_tampered_reference_tables_never_reach_the_grader(self):
        execution = OpsKnowledgeExecution.__new__(OpsKnowledgeExecution)
        facts = {'columns': ['count'], 'rows': [[3]]}
        good = {'id': 'P01', **facts, 'facts_sha256': digest(facts)}
        for probe in ({**good, 'columns': ['*']}, {**good, 'rows': [[3, 4]]}, {**good, 'rows': [[5]]}):
            with self.subTest(probe=probe), self.assertRaisesRegex(Failure, 'independent_reference_shape_or_hash_invalid'):
                execution.check_reference({'status': 'reviewed', 'verification': {'probes': [probe]}})

    def test_task_latency_excludes_setup_conversation_creation_and_grading(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [0]
            case = load_case('J3-01')
            execution = KnowledgeExecution(Mock(requests=0), case, Evidence(tmp), tmp, 'model')
            def setup():
                clock[0] += 100
            def conversation(_):
                clock[0] += 30
                return 'session'
            def send(*_):
                clock[0] += 5
                entry = {'task_id': 'task', 'conversation_id': 'session', 'seconds': 5, 'phase': 'question',
                         'state': 'completed', 'capture_complete': True}
                execution.tasks.append(entry)
                return entry, {}
            def grade(_):
                clock[0] += 20
                execution.report['status'] = 'passed'
            with patch('journey.knowledge_case.time.monotonic', side_effect=lambda: clock[0]), \
                    patch.object(execution, 'setup', side_effect=setup), \
                    patch.object(execution, 'conversation', side_effect=conversation), \
                    patch.object(execution, 'send', side_effect=send), \
                    patch.object(execution, 'grade_answers', side_effect=grade), \
                    patch.object(execution, 'finalize'), patch.object(execution, 'capture_trajectories'), \
                    patch('journey.knowledge_case.finish_version'), \
                    patch('journey.knowledge_case.final_reply', return_value='Answer'):
                result = execution.run()
            self.assertEqual(result['task_seconds'], 5)
            self.assertEqual(clock[0], 155)

    def test_admission_failure_keeps_request_trace_session_and_attempt_latency(self):
        from journey.benchmark_metrics import history_record
        case = load_case('J3-01')
        result = _missing_case(case, 'journey-3', 'admission_rejected')
        result.update(agent_attempted=True, task_seconds=5, admission_failures=[{
            'conversation_id': 'session', 'trace_headers': {'X-Trace-ID': 'request-trace'}}])
        with tempfile.TemporaryDirectory() as tmp:
            row = history_record(result, case, Path(tmp) / 'events.jsonl')
        self.assertEqual(row['trace_id'], 'request-trace')
        self.assertEqual(row['session_id'], 'session')
        self.assertEqual(row['duration_seconds'], 5)

    def test_compact_skill_bindings_are_matched_by_scoped_id_without_requiring_display_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = load_case('J3-88')
            case.update(ops_target={'agent_id': 'clone', 'model_ids': [5], 'tables': ['fixture.rows'],
                'required_skill_refs': [{'id': 's1', 'workspace_id': 'system'}],
                'expected_enabled_tables': {'5': ['fixture.rows']}},
                snapshot_id='s1', snapshot_manifest_sha256='expected')
            client = Mock(requests=0)
            client.product.return_value = {'id': 'clone', 'name': 'Private fixture', 'workspace_id': 'ws',
                'status': 'active', 'binding': {'knowledge_base_refs': [{'id': '5', 'workspace_id': 'ws'}],
                    'skill_refs': [{'id': 's1', 'workspace_id': 'system', 'kind': 'skill'}]}}
            execution = OpsKnowledgeExecution(client, case, Evidence(tmp), tmp, 'model')
            execution.report['rag'] = {}
            with patch('journey.ops_knowledge_case.discover_workspace', return_value='/workspaces/ws'), \
                    patch('journey.benchmark_store.Store.dataset', return_value={'state': 'complete', 'manifest_sha256': 'expected'}), \
                    patch('journey.ops_knowledge_case.list_sources', return_value=[{'db_name': 'fixture', 'table_name': 'rows',
                          'effective_enabled': True, 'ingest_status': 'succeeded'}]), \
                    patch('journey.ops_knowledge_case.start_version'):
                execution.setup()
            self.assertEqual(execution.report['benchmark']['state'], 'eligible')

    def test_private_catalog_never_accepts_a_different_revision_or_tampered_question(self):
        spec = {'id': 'J3-88', 'workspace': 'journey-3', 'question': 'PRIVATE-QUESTION'}
        case = {'id': 'J3-88', 'private_spec': {'revision': 'r1', 'sha256': digest(spec)}}
        store = Store.__new__(Store)
        store.sql = Mock(return_value={'rows': [[canonical(spec), digest(spec)]]})
        self.assertEqual(store.spec(case), spec)
        store.sql.return_value['rows'][0][0] = canonical({**spec, 'question': 'TAMPERED'})
        with self.assertRaisesRegex(Failure, 'private_case_integrity_mismatch'):
            store.spec(case)

    def test_catalog_hydrates_only_the_checksum_verified_private_spec(self):
        spec = {'id': 'J3-88', 'workspace': 'journey-3', 'timeout_seconds': 600,
                'assets': [], 'oracles': [], 'sessions': []}
        stub = {**spec, 'private_spec': {'revision': 'r1', 'sha256': digest(spec)}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'cases/journey-3/J3-88/case.json'
            path.parent.mkdir(parents=True)
            path.write_text(canonical(stub))
            (root / 'suites.json').write_text(canonical({'model': 'test', 'case_timeout_seconds': 600,
                'journeys': [{'workspace': 'journey-3', 'case_timeout_override_seconds': 1800}]}))
            private = root / 'private.json'
            private.write_text(canonical(spec))
            with patch('journey.catalog.ROOT', root), patch.dict(os.environ, {'MOI_PRIVATE_CASE_SPEC': str(private)}):
                case = load_case('J3-88')
                self.assertEqual(case['private_spec'], stub['private_spec'])
                self.assertEqual(case['timeout_seconds'], 1800)
                private.write_text(canonical({**spec, 'sessions': ['changed']}))
                with self.assertRaisesRegex(ValueError, 'private_case_integrity_mismatch'):
                    load_case('J3-88')

    def test_missing_dependencies_or_pending_snapshot_do_not_become_agent_failures_or_scores(self):
        for variant in ('tool', 'snapshot', 'scope', 'attachment'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                case = load_case('J3-88')
                case.update(ops_target={'agent_id': 'clone', 'model_ids': [5], 'tables': ['fixture.rows'],
                            'required_tool_refs': [{'id': 'missing', 'workspace_id': 'ws'}] if variant == 'tool' else []},
                            snapshot_id='s1', snapshot_manifest_sha256='expected',
                            benchmark_eligibility='external_dependency' if variant == 'scope' else 'knowledge_sql',
                            required_inputs_state='missing' if variant == 'attachment' else 'ready')
                client = Mock(requests=0)
                client.product.return_value = {'id': 'clone', 'name': 'Private fixture', 'workspace_id': 'ws',
                    'status': 'active', 'binding': {'knowledge_base_refs': [{'id': '5', 'workspace_id': 'ws'}]}}
                evidence = Evidence(Path(tmp) / 'results' / case['id'])
                execution = OpsKnowledgeExecution(client, case, evidence, tmp, 'model')
                with patch('journey.ops_knowledge_case.discover_workspace', return_value='/workspaces/ws'), \
                        patch('journey.benchmark_store.Store.dataset', return_value={'state': 'preparing', 'manifest_sha256': 'expected'}), \
                        patch('journey.ops_knowledge_case.start_version'), \
                        patch('journey.report.load_case', return_value=case):
                    result = execution.run()
                    normalized = finish_case(case['id'], Path(tmp) / 'results')
                self.assertEqual(result['benchmark']['state'], 'out_of_scope' if variant == 'scope' else 'blocked')
                self.assertEqual(normalized['test_status'], 'completed')
                self.assertEqual(normalized['rag']['answers'], [])
                self.assertFalse(normalized['harness_errors'])
                self.assertFalse(execution.tasks)
                client.stream_rpc.assert_not_called()

    def test_sql_citations_keep_native_attestation_distinct_from_unobserved_source_bodies(self):
        reference = {'type': 'sql_table', 'database': 'fixture', 'table': 'fixture.rows',
                     'artifact_id': 'sql-source', 'sql': 'SELECT COUNT(*) FROM fixture.rows'}
        observed = {'selected_read_receipt': True, 'citations': [
            {'reference': reference}, {'reference': {**reference, 'table': 'other.rows'}},
            {'reference': {'type': 'rag_chunk'}}]}
        validate_sql_citations(observed, ['fixture.rows'])
        metric = citation_integrity_metric(observed)
        self.assertEqual(metric, {'valid': 1, 'total': 2, 'expected_total': 3, 'unavailable': 1, 'rate': .5})
        self.assertNotIn('source_evidence_ids', observed['citations'][0])
        observed['selected_read_receipt'] = False
        validate_sql_citations(observed, ['fixture.rows'])
        self.assertIsNone(observed['citations'][0]['integrity'])

    def test_scored_citation_metrics_survive_public_projection_without_source_text(self):
        result = _missing_case(load_case('J3-01'), 'journey-3', 'private')
        result['rag']['answers'] = [{'session': 'qa', 'phase': 'q', 'question': 'PRIVATE', 'text': 'PRIVATE',
            'task_id': 'private', 'evaluation_state': 'scored', 'verdict': {}, 'metrics': {
                name: ({'valid': 1, 'total': 1, 'rate': 1} if name == 'citation_integrity' else
                       {'passed': 1, 'total': 1, 'rate': 1})
                for name in ('correctness', 'faithfulness', 'citation_support', 'citation_integrity')}}]
        projected = public_result(result)
        self.assertTrue(valid_rag_result(projected['rag']))
        self.assertNotIn('PRIVATE', canonical(projected))
        bad = copy.deepcopy(result)
        bad['task_seconds'] = 'PRIVATE'
        with self.assertRaises(ValueError):
            public_result(bad)

    def test_history_pagination_and_downloaded_metric_files_never_copy_unexpected_columns(self):
        row = ['J3-88', '2026-11-18 00:00:00', '1', '75', '2', '3', 'completed',
               'r1', 's1', 'judge1', 'a' * 32, 'eligible']
        store = Mock()
        store.sql.side_effect = [{'rows': [[f'{i:064x}', *row] for i in range(2000)]},
                                {'rows': [['f' * 64, *row]]}]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(export(store, tmp), 2001)
            self.assertIn('result_key>', store.sql.call_args.args[0])
            data = json.loads((Path(tmp) / 'history.json').read_text())
            self.assertEqual(set(data[0]), set(FIELDS))
            html = (Path(tmp) / 'history.html').read_text()
            jobs = json.loads(html.split('const jobs = ', 1)[1].split(';', 1)[0])
            self.assertEqual(jobs, {
                'documents': [f'J3-{i:02d}' for i in range(1, 58)],
                'operations': [f'J3-{i:02d}' for i in range(58, 118)],
            })
            with self.assertRaisesRegex(ValueError, 'invalid_exported_history_columns'):
                write_metrics([{**metrics_row(row), 'question': 'PRIVATE'}], tmp)

    def test_real_query_time_wire_normalizes_to_utc_and_sql_pagination_literals(self):
        row = ['J3-88', '2026-11-18 00:00:00.123456 +0000 UTC', '1', None, None, None,
               'unknown', 'r1', 's1', 'judge1', 'a' * 32, 'blocked']
        self.assertEqual(metrics_row(row)['evaluated_at'], '2026-11-18T00:00:00.123456Z')
        row[1] = '2026-11-18T08:00:00+08:00'
        with self.assertRaisesRegex(ValueError, 'history_requires_UTC'):
            metrics_row(row)


if __name__ == '__main__':
    unittest.main()
