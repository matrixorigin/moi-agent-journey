import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from journey.benchmark_details import retained_report
from journey.benchmark_metrics import delivered_answer_seconds, history_observations, history_record, model_rounds, quality_score
from journey.benchmark_store import Store, canonical, digest, history_diagnostic
from journey.benchmark_history import metrics_row, read_run_metadata, write_metrics
from journey.benchmark_run_report import details_for_case, publish, run_details
from journey.catalog import load_case
from journey.client import Failure, error_record
from journey.publish import prepare_publication
from journey.report import ArtifactRejected, _missing_case, _valid_case_result


class BenchmarkTests(unittest.TestCase):
    def test_history_diagnostic_keeps_transport_facts_without_query_or_response_text(self):
        client = Mock()
        client.request.side_effect = Failure('invalid_response', 200,
            details={'content_type': 'text/html', 'body': 'PRIVATE RESPONSE'},
            trace_headers={'x-request-id': 'PRIVATE ID'})
        with self.assertRaises(Failure) as error:
            Store(client, '/fixture').sql("INSERT INTO results VALUES ('PRIVATE QUERY')")
        body = client.request.call_args.args[2]
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / 'history.log'
            log_path.with_suffix('.error.json').write_text(canonical(error_record('history', error.exception)))
            self.assertEqual(history_diagnostic(log_path), {
                'reason': 'invalid_response', 'http_status': 200, 'content_type': 'text/html',
                'operation': 'INSERT', 'request_bytes': len(json.dumps(body).encode()),
            })

    def test_private_business_data_cannot_escape_via_reports_traces_paths_or_outputs(self):
        marker='PRIVATE_BUSINESS_e4f9'
        case=load_case('J3-01');case.update(id='J3-58',private_spec={'revision':'test'})
        result=_missing_case(case,'journey-3',marker)
        result['title']=marker
        result['tasks']=[{'task_id':marker,'trace_id':marker,'conversation_id':marker}]
        result['rag']['answers']=[{'session':'qa','phase':'question','question':marker,'text':marker,
            'task_id':marker,'conversation_id':marker,'trace_id':marker,'evaluation_state':'unavailable',
            'evaluation_reason':marker,'metrics':{}}]
        self.assertTrue(_valid_case_result(result))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';private=source/case['id'];private.mkdir(parents=True)
            (private/'result.json').write_text(canonical(result))
            (private/'history-receipt.json').write_text(canonical({'state':'saved','result_key':'a'*64}))
            (private/(marker+'.json')).write_text(marker)
            (private/'events.jsonl').write_text(canonical({'kind':'text','payload':{'text':marker}})+'\n')
            (private/'outputs').mkdir();(private/'outputs'/'delivery.zip').write_bytes(marker.encode())
            with patch('journey.catalog.load_case',return_value=case):
                prepare_publication(source,root/'public',())
            files=list((root/'public').rglob('*'))
            self.assertTrue(files)
            for f in files:
                self.assertNotIn(marker,str(f))
                if f.is_file():self.assertNotIn(marker.encode(),f.read_bytes())
            projected=json.loads((root/'public'/case['id']/'result.json').read_text())
            self.assertEqual(projected['privacy'],'metrics_only')
            self.assertTrue(_valid_case_result(projected))

    def test_missing_history_receipt_is_an_explicit_harness_failure(self):
        case=load_case('J3-01');case.update(id='J3-58',private_spec={'revision':'test'})
        result=_missing_case(case,'journey-3','unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';d=source/case['id'];d.mkdir(parents=True)
            (d/'result.json').write_text(canonical(result))
            with patch('journey.catalog.load_case',return_value=case):prepare_publication(source,root/'public',())
            public=json.loads((root/'public'/case['id']/'result.json').read_text())
            self.assertIn({'stage':'history','reason':'QA_history_not_saved'},public['harness_errors'])

    def test_no_score_and_no_tool_observations_are_null_and_still_persistable(self):
        case=load_case('J3-01');result=_missing_case(case,'journey-3','QA_unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            row=history_record(result,case,Path(tmp)/'events.jsonl')
        self.assertIsNone(row['quality_score']);self.assertIsNone(row['tool_call_count'])
        self.assertIsNone(row['model_round_count']);self.assertTrue(row['failure_reason'])
        self.assertEqual(quality_score({'rag':{'answers':[{'metrics':{'correctness':{'passed':1,'total':2,'unavailable':1}}}]}}),None)

    def test_model_rounds_deduplicate_starts_finishes_and_exclude_the_judge(self):
        rows=[]
        def add(task,seq,kind,**extra):rows.append({'task_id':task,'seq':seq,'kind':'lifecycle','payload':{'type':kind,**extra}})
        add('a',1,'llm_started');add('a',2,'llm_finished',call_id='one')
        add('a',2,'llm_finished',call_id='one');add('a',3,'llm_started',call_id='two')
        add('judge',1,'llm_finished',call_id='not_agent')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'events.jsonl';p.write_text('\n'.join(canonical(r) for r in rows))
            self.assertEqual(model_rounds(p,[{'task_id':'a','phase':'qa'},{'task_id':'judge','phase':'judge:quality'}]),2)

    def test_sql_success_wire_is_decoded_and_history_replays_do_not_duplicate_evidence(self):
        store=Store.__new__(Store);store.client=Mock()
        data = {'status': 'success', 'result': [[{'String': '42', 'Valid': True}, {'String': '', 'Valid': False}]]}
        for code in ('OK', 200):
            with self.subTest(code=code):
                store.client.request.return_value = {'code': code, 'data': data}
                self.assertEqual(store.sql('SELECT 42,NULL')['rows'], [['42', None]])
        case=load_case('J3-01');result=_missing_case(case,'journey-3','offline')
        store.sql=Mock(return_value={'rows':[[canonical({**result,'pipeline':{'stages':{}}})]]})
        store.upload_private=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(store.persist(result,case,tmp)['state'],'already_saved')
        store.upload_private.assert_not_called()

    def test_history_persists_report_facts_and_lookup_ids_without_raw_evidence(self):
        case = load_case('J3-01')
        result = _missing_case(case, 'journey-3', 'fixture')
        result.update(task={'state': 'completed'}, task_seconds=12.5, tasks=[
            {'task_id': 'task', 'conversation_id': 'session', 'trace_id': 'trace', 'state': 'completed'}])
        result['rag']['answers'] = [{'task_id': 'task', 'text': 'PRIVATE_BODY', 'evaluation_state': 'scored',
            'metrics': {'correctness': {'passed': 0, 'total': 1}}, 'verdict': {'criteria': [
                {'id': 'C1', 'reason': 'Missing a required fact', 'passed': False, 'answer_quote': 'PRIVATE_QUOTE'}]}}]
        result['tools'] = {'metrics': {'failed': 1, 'succeeded': 1}, 'errors': [
            {'task_id': 'task', 'tool': 'query_sql', 'outcome': 'failed', 'arguments': {'query': 'select 1'},
             'errors': [{'kind': 'error', 'detail': 'Invalid query'}], 'result': 'PRIVATE_RAW_RESULT'},
        ]}
        store = Store.__new__(Store)
        store.upload_private = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / 'events.jsonl'
            row = history_record(result, case, events)
            observations = history_observations(result, row, events)
            saved = {**retained_report(result, row, observations), 'result_sha256': digest(result)}
            store.sql = Mock(side_effect=[{'rows': []}, {}, {'rows': [[canonical(saved)]]},
                                          {'rows': [[canonical({**saved, 'pipeline': {'stages': {}},
                                              'history_observations': {**observations, 'completed_seconds': 12.5,
                                                                       'answer_timing': 'final_answer_sse'}})]]}])
            self.assertEqual(store.persist(result, case, tmp)['state'], 'saved')
            self.assertEqual(store.persist(result, case, tmp)['state'], 'already_saved')
        insert = store.sql.call_args_list[1].args[0]
        self.assertNotIn('PRIVATE_', insert)
        self.assertNotIn('evidence_file_id', insert)
        self.assertNotIn('completed_seconds', insert)
        self.assertNotIn('answer_timing', insert)
        self.assertEqual(row['duration_seconds'], 12.5)
        self.assertIn('Missing a required fact', insert)
        self.assertIn('Invalid query', insert)
        self.assertIn('"task_id":"task"', insert)
        self.assertEqual(details_for_case(row, saved), details_for_case(row, result))
        self.assertEqual(store.sql.call_count, 4)
        store.upload_private.assert_not_called()

    def test_pipeline_finalization_preserves_execution_and_records_late_failure(self):
        case = load_case('J3-01')
        result = _missing_case(case, 'journey-3', 'fixture')
        result.update(test_status='completed', task={'state':'completed'}, harness_errors=[])
        stored = {**result, 'history_observations': {'qa_outcome':'completed'}}
        stages = {'history': {'exit_code':1,'timed_out':False},
                  'publish': {'exit_code':0,'timed_out':False}}
        updated = {**stored, 'pipeline': {'stages':stages}}
        store = Store.__new__(Store)
        store.sql = Mock(side_effect=[{'rows':[[canonical(stored)]]}, {},
                                      {'rows':[[canonical(updated)]]}])
        store.finalize_pipeline(result, case, stages)
        statement = store.sql.call_args_list[1].args[0]
        self.assertIn('UPDATE journey3_benchmark.results SET report_json=', statement)
        self.assertIn(' AND report_json=', statement)
        self.assertEqual(updated['task'], stored['task'])
        self.assertEqual(updated['quality'], stored['quality'])
        self.assertEqual(updated['history_observations'], stored['history_observations'])
        row = {'case_id':'J3-01','task_state':'completed','benchmark_state':'eligible',
               'duration_seconds':10,'model_round_count':1,'quality_score':None,'session_id':None,'trace_id':None}
        details = details_for_case(row, updated)
        self.assertIn('history', details['errors'][0][-1])
        self.assertIn('取证发布流程', details['errors'][0][-1])

    def test_pipeline_finalization_requires_a_verified_compare_and_swap(self):
        from journey.client import Failure
        case = load_case('J3-01')
        result = _missing_case(case, 'journey-3', 'fixture')
        store = Store.__new__(Store)
        store.sql = Mock(side_effect=[{'rows':[[canonical(result)]]}, {},
                                      {'rows':[[canonical(result)]]}])
        with self.assertRaises(Failure) as raised:
            store.finalize_pipeline(result, case, {'publish':{'exit_code':1,'timed_out':False}})
        self.assertEqual(raised.exception.code, 'pipeline_history_confirmation_failed')

    def test_late_failure_only_updates_compact_report_metadata(self):
        case = load_case('J3-01')
        result = _missing_case(case, 'journey-3', 'fixture')
        stored = {'case_id': 'J3-01', 'workspace': 'journey-3', 'tasks': [],
                  'report_details': {'errors': [], 'timeouts': [], 'tools': [], 'scores': []}}
        stages = {'publish': {'exit_code':1,'timed_out':False}}
        updated = {**stored, 'pipeline': {'stages': stages}}
        store = Store.__new__(Store)
        store.upload_private = Mock()
        store.sql = Mock(side_effect=[{'rows':[[canonical(stored)]]}, {},
                                      {'rows':[[canonical(updated)]]}, {'rows':[[canonical(updated)]]}])
        store.finalize_pipeline(result, case, stages)
        store.finalize_pipeline(result, case, stages)
        store.upload_private.assert_not_called()
        statement = store.sql.call_args_list[1].args[0]
        self.assertNotIn('"evidence":', statement)
        details = details_for_case({'case_id':'J3-01','session_id':None,'trace_id':None}, updated)
        self.assertIn('publish', details['errors'][0][-1])

    def test_history_public_metrics_reject_text_in_numeric_or_revision_columns(self):
        good=['J3-58','2026-11-18 00:00:00','12.5','75','4','3','completed','rev1','snap1','judge1',
              'a'*32,'eligible','44','1','completed','3','3','1']
        self.assertEqual(metrics_row(good)['quality_score'],75)
        for i,value in [(3,'business secret'),(7,'<script>business</script>'),(1,'business secret')]:
            bad=copy.copy(good);bad[i]=value
            with self.subTest(i=i),self.assertRaises(ValueError):metrics_row(bad)

    def test_history_looks_up_run_numbers_without_copying_other_github_fields(self):
        rows = [{'run_id': '44'}]
        payload = {'id': 44, 'run_number': 96, 'run_attempt': 2,
                   'created_at': '2026-11-19T14:54:14Z', 'run_started_at': '2026-11-19T15:00:00Z',
                   'unrelated': 'ignored'}
        with patch('journey.benchmark_history.subprocess.run',
                   return_value=Mock(stdout=canonical(payload)+'\n'+canonical({**payload,'id':45}))) as request:
            metadata = read_run_metadata(rows, 'owner/repo')
        self.assertEqual(metadata, {'44': {'number': 96, 'attempt': 2,
            'created_at': '2026-11-19T14:54:14+00:00', 'started_at': '2026-11-19T15:00:00+00:00'}})
        self.assertIn('repos/owner/repo/actions/workflows/journeys.yml/runs?per_page=100', request.call_args.args[0])

    def test_body_in_earlier_turn_counts_as_completion_and_limits_completion_timing(self):
        report = {'tasks': [{'task_id': 'answered'}, {'task_id': 'timed-out'}], 'task': {'state': 'timeout'},
                  'task_seconds': 60,
                  'rag': {'answers': [{'task_id': 'answered', 'text': 'Delivered', 'seconds': 3.5},
                                      {'task_id': 'context', 'text': 'Context', 'seconds': 8,
                                       'evaluation_state': 'context_only'}]}}
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / 'events.jsonl'
            events.write_text('\n'.join(json.dumps({'task_id': i, 'seq': seq, 'kind': 'lifecycle',
                'payload': {'type': 'llm_started'}}) for seq, i in enumerate(['answered', 'timed-out', 'timed-out'])))
            row = history_record(report, load_case('J3-01'), events)
            observed = history_observations(report, row, events)
        self.assertEqual(observed['qa_outcome'], 'completed')
        self.assertEqual(row['duration_seconds'], 3.5)
        self.assertEqual(observed['completed_model_rounds'], 1)

    def test_delivery_timing_keeps_multiturn_clock_and_missing_samples_distinct(self):
        answers = [{'text': 'Context', 'seconds': 2, 'evaluation_state': 'context_only'},
                   {'text': 'Answer', 'seconds': 5, 'evaluation_state': 'scored'}]
        self.assertEqual(delivered_answer_seconds(answers), 7)
        answers[0]['seconds'] = None
        self.assertIsNone(delivered_answer_seconds(answers))

    def test_report_reuses_qa_html_as_a_single_github_file(self):
        secret_marker = '</script><PRIVATE_FACT>'
        report = {
            'tasks': [{'task_id': 'run-qa', 'conversation_id': 'task-session', 'trace_id': 'task-trace'}],
            'tools': {'errors': [{'outcome': 'unknown'}, {
                'outcome': 'failed', 'task_id': 'run-qa', 'tool': 'bash',
                'arguments': {'command': secret_marker},
                'errors': [{'code': 'E_TIMEOUT', 'detail': 'original error'}],
            }]},
        }
        row = {'case_id': 'J3-01', 'task_state': 'timeout', 'benchmark_state': 'eligible',
               'duration_seconds': 60.0, 'model_round_count': 2, 'quality_score': None,
               'session_id': 'case-session', 'trace_id': 'case-trace'}
        details = details_for_case(row, report)
        self.assertFalse(details['errors'])
        self.assertEqual(details['timeouts'], [['J3-01', 'task-session', 'task-trace', 60]])
        saved = retained_report(report, row, {'qa_outcome': 'timeout'})
        self.assertEqual(saved['report_details']['timeouts'], [['J3-01', 'task-session', 'task-trace']])
        self.assertEqual(details_for_case(row, saved)['timeouts'], details['timeouts'])
        self.assertEqual(details['tools'][0][1:5], ['task-session', 'task-trace', 'bash', secret_marker])
        store = Mock()
        raw = canonical(report)
        store.sql.return_value = {'rows': [[*row.values(), raw, len(raw)]]}
        with tempfile.TemporaryDirectory() as tmp, \
                patch('journey.benchmark_run_report.load_suite', return_value={'cases': ['J3-01', 'J3-02']}):
            out = Path(tmp) / 'upload'
            filename, missing = publish(store, [{'case_id': 'J3-01', 'run_id': '44', 'run_attempt': 2}], '44', 2, out)
            self.assertEqual(list(out.iterdir()), [out / filename])
            self.assertEqual((out / filename).read_bytes(), store.upload_private.call_args.args[1])
        self.assertEqual(missing, ['J3-02'])
        self.assertIn("run_attempt=2", store.sql.call_args.args[0])
        self.assertEqual(filename, 'journey-3-report-44-attempt-2.html')
        filename_arg, html, content_type = store.upload_private.call_args.args
        self.assertEqual(filename_arg, filename)
        self.assertEqual(content_type, 'text/html; charset=utf-8')
        self.assertIn(b'original error', html)
        self.assertIn(b'task-trace', html)
        self.assertNotIn(secret_marker.encode(), html)
        self.assertIn(b'"environment": "QA"', html)
        self.assertIn(b'"coverage": {"recorded": 1, "planned": 2, "missing": ["J3-02"]}', html)
        with tempfile.TemporaryDirectory() as tmp:
            write_metrics([], tmp)
            public = (Path(tmp) / 'history.html').read_bytes()
        self.assertNotIn(b'original error', public)
        self.assertNotIn(b'PRIVATE_FACT', public)
        self.assertNotIn(b'task-trace', public)

    def test_report_reads_oversized_cases_in_bounded_character_chunks(self):
        message = '超过上下文长度'
        raw = json.dumps({'harness_errors': [{'stage': 'semantic_judge', 'reason': 'context_limit',
                                             'details': message}]}, ensure_ascii=False)
        oversized = ['J3-01', 'completed', 'eligible', '12', '2', None, 'session', 'trace', None, len(raw)]
        small = ['J3-02', 'completed', 'eligible', '3', '1', None, 'session-2', 'trace-2', '{}', 2]
        chunk_size = 20
        chunks = [raw[offset:offset+chunk_size] for offset in range(0, len(raw), chunk_size)]
        store = Mock()
        store.sql.side_effect = [{'rows': [oversized, small]}, *({'rows': [[part]]} for part in chunks)]
        with patch('journey.benchmark_run_report.REPORT_CHUNK_CHARS', chunk_size):
            details = run_details(store, '44', 2)
        self.assertEqual(len(details['errors']), 1)
        self.assertEqual(details['errors'][0][:3], ['J3-01', 'session', 'trace'])
        self.assertIn(message, details['errors'][0][3])
        for index, call in enumerate(store.sql.call_args_list[1:]):
            self.assertIn(f'SUBSTRING(report_json,{1+index*chunk_size},{len(chunks[index])})', call.args[0])
            self.assertIn("run_id='44' AND run_attempt=2 AND case_id='J3-01'", call.args[0])

    def test_report_with_credentials_is_not_staged_for_github(self):
        store = Mock()
        with tempfile.TemporaryDirectory() as tmp, \
                patch('journey.benchmark_run_report.run_details', return_value={}), \
                patch('journey.benchmark_run_report.render_history', return_value='sk-uca-v1-' + 'a' * 24):
            out = Path(tmp) / 'upload'
            with self.assertRaises(ArtifactRejected):
                publish(store, [], '44', 2, out)
            self.assertFalse(out.exists())
        store.upload_private.assert_called_once()

    def test_production_report_publishes_metrics_without_session_details(self):
        details = {'run_id': '44', 'attempt': 1, 'errors': [['J3-01', 'private-session', 'private-trace', 'private-fact']],
                   'timeouts': [], 'tools': [], 'scores': []}
        store = Mock()
        rows = [{'case_id': 'J3-01', 'run_id': '44', 'run_attempt': 1}]
        with tempfile.TemporaryDirectory() as tmp, \
                patch('journey.benchmark_run_report.run_details', return_value=details), \
                patch('journey.benchmark_run_report.load_suite', return_value={'cases': ['J3-01']}):
            filename, missing = publish(store, rows, '44', 1, Path(tmp), production=True)
            published = (Path(tmp) / filename).read_bytes()
        self.assertEqual(missing, [])
        self.assertIn(b'private-fact', store.upload_private.call_args.args[1])
        self.assertNotIn(b'private-fact', published)
        self.assertNotIn(b'private-session', published)
        self.assertNotIn(b'private-trace', published)
        self.assertIn(b'"environment": "Production"', published)
        self.assertIn(b'"report_error_cases": 1', published)
        self.assertIn(b'"metrics_only": true', published)


if __name__=='__main__':unittest.main()
