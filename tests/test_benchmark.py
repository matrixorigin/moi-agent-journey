import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from journey.benchmark_metrics import history_record, model_rounds, quality_score
from journey.benchmark_store import Store, canonical
from journey.benchmark_history import metrics_row
from journey.catalog import load_case
from journey.publish import prepare_publication
from journey.report import _missing_case, _valid_case_result


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
        store.client.product.return_value={'status':'success','result':[[{'String':'42','Valid':True},{'String':'','Valid':False}]]}
        decoded=store.sql('SELECT 42,NULL');self.assertEqual(decoded['rows'],[['42',None]])
        case=load_case('J3-01');result=_missing_case(case,'journey-3','offline')
        store.sql=Mock(return_value={'rows':[[canonical(result)]]});store.upload=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(store.persist(result,case,tmp)['state'],'already_saved')
        store.upload.assert_not_called()

    def test_history_public_metrics_reject_text_in_numeric_or_revision_columns(self):
        good=['J3-58','2026-11-18 00:00:00','12.5','75','4','3','completed','rev1','snap1','judge1','a'*32,'eligible']
        self.assertEqual(metrics_row(good)['quality_score'],75)
        for i,value in [(3,'business secret'),(7,'<script>business</script>'),(1,'business secret')]:
            bad=copy.copy(good);bad[i]=value
            with self.subTest(i=i),self.assertRaises(ValueError):metrics_row(bad)


if __name__=='__main__':unittest.main()
