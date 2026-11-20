import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from journey.benchmark_metrics import history_observations, history_record, model_rounds, quality_score
from journey.benchmark_store import Store, canonical
from journey.benchmark_history import metrics_row, read_run_metadata, write_metrics
from journey.benchmark_run_report import details_for_case, publish
from journey.catalog import load_case
from journey.publish import prepare_publication
from journey.report import ArtifactRejected, _missing_case, _valid_case_result


class BenchmarkTests(unittest.TestCase):
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
        store.sql=Mock(return_value={'rows':[[canonical(result)]]});store.upload=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(store.persist(result,case,tmp)['state'],'already_saved')
        store.upload.assert_not_called()

    def test_history_public_metrics_reject_text_in_numeric_or_revision_columns(self):
        good=['J3-58','2026-11-18 00:00:00','12.5','75','4','3','completed','rev1','snap1','judge1',
              'a'*32,'eligible','44','1','completed','12.5','3','3','1']
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
        report = {'tasks': [{'task_id': 'answered'}, {'task_id': 'timed-out'}],
                  'rag': {'answers': [{'task_id': 'answered', 'text': 'Delivered', 'seconds': 3.5},
                                      {'task_id': 'context', 'text': 'Context', 'seconds': 8,
                                       'evaluation_state': 'context_only'}]}}
        row = {'task_state': 'timeout', 'benchmark_state': 'eligible', 'duration_seconds': 60, 'model_round_count': 4}
        with tempfile.TemporaryDirectory() as tmp:
            events = Path(tmp) / 'events.jsonl'
            events.write_text('\n'.join(json.dumps({'task_id': i, 'seq': seq, 'kind': 'lifecycle',
                'payload': {'type': 'llm_started'}}) for seq, i in enumerate(['answered', 'timed-out', 'timed-out'])))
            observed = history_observations(report, row, events)
        self.assertEqual(observed['qa_outcome'], 'completed')
        self.assertEqual(observed['completed_seconds'], 3.5)
        self.assertEqual(observed['completed_model_rounds'], 1)

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
        self.assertEqual(details['tools'][0][1:5], ['task-session', 'task-trace', 'bash', secret_marker])
        store = Mock()
        store.sql.return_value = {'rows': [[*row.values(), canonical(report)]]}
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
        self.assertIn(b'"metrics_only": true', published)


if __name__=='__main__':unittest.main()
