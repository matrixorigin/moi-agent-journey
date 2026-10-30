import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from journey.catalog import ROOT, load_case, load_suite, local_asset, prepare
from journey.business import saved_checks, validate_verdict
from journey.comparison import build, current_state, load_baseline, publish
from journey.report import aggregate
from journey.evidence import Evidence
from journey.run_case import Execution


class BusinessJourneyTests(unittest.TestCase):
    def test_all_100_cases_have_verified_inputs_and_withheld_rubrics(self):
        suite = load_suite('journey-4')
        self.assertEqual(suite['cases'],[f'J4-{n:02d}' for n in range(1,101)])
        self.assertEqual(suite['concurrency'],3)
        for cid in suite['cases']:
            case = load_case(cid)
            self.assertEqual(case['timeout_seconds'],1800)
            self.assertGreaterEqual(len(case['sessions'][0]['rubric']),5)
            self.assertEqual(case['source']['task'],f'T{int(cid[3:]):03d}')
            for asset in case['assets']:
                self.assertTrue(local_asset(asset).is_file())
                self.assertNotIn('baseline',asset['local_path'])
            prompt = case['sessions'][0]['turns'][0]['prompt']
            self.assertNotIn('Codex',prompt)
            self.assertNotIn('99通过',prompt)

    def test_original_198_material_hashes_are_preserved(self):
        root = ROOT/'cases/journey-4/_materials'
        manifest = json.loads((root/'manifest.json').read_text())
        self.assertEqual(len(manifest),198)
        self.assertEqual(len(list((root/'samples').rglob('*.*'))),198)
        for item in manifest:
            p=root/item['path']
            self.assertEqual(p.stat().st_size,item['bytes'])
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),item['sha256'])

    def test_local_asset_rejects_path_escape_and_bad_hash(self):
        with self.assertRaises(ValueError):
            local_asset({'local_path':'/etc/passwd','sha256':'bad'})
        with self.assertRaises(ValueError):
            local_asset({'local_path':'cases/../../suites.json','sha256':'bad'})
        asset = dict(load_case('J4-01')['assets'][0]);asset['sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'checksum'):
            local_asset(asset)

    def test_zip_contains_only_selected_inputs_not_answers_or_rubrics(self):
        case = load_case('J4-61')
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);prepare(case,work)
            class Client:
                def upload(self,root,path):
                    with zipfile.ZipFile(path) as archive:
                        self.names=archive.namelist()
                    return {'file_id':'test'}
            client=Client();execution=Execution(client,case,Evidence(work/'results'),work,'model')
            execution.session_input(case['sessions'][0],0)
            self.assertIn('inputs/samples/shared/meeting.wav',client.names)
            self.assertNotIn('inputs/samples/shared/meeting.txt',client.names)
            self.assertFalse(any('baseline' in n or 'case.json' in n or 'rubric' in n for n in client.names))

    def test_semantic_judge_rejects_hallucinated_or_incomplete_evidence(self):
        criteria={'C01':'criterion'};outputs={'result.md':'actual delivered text'}
        value={'checks':[{'id':'C01','passed':True,'reason':'verified','evidence_file':'result.md','evidence_quote':'delivered'}]}
        self.assertTrue(validate_verdict(json.dumps(value),criteria,outputs)[0]['passed'])
        for field,value_to_set in [('evidence_quote','invented'),('evidence_file','../answer.md'),('passed','true')]:
            changed=json.loads(json.dumps(value));changed['checks'][0][field]=value_to_set
            with self.assertRaises(ValueError):validate_verdict(json.dumps(changed),criteria,outputs)
        with self.assertRaises(ValueError):validate_verdict('{"checks":[]}',criteria,outputs)
        changed=json.loads(json.dumps(value));changed['checks']*=2
        with self.assertRaises(ValueError):validate_verdict(json.dumps(changed),criteria,outputs)
        failed={'checks':[{'id':'C01','passed':False,'reason':'unsupported'}]}
        self.assertFalse(validate_verdict(json.dumps(failed),criteria,outputs)[0]['passed'])

    def test_baseline_has_all_reviews_and_verified_public_evidence(self):
        baseline,_ = load_baseline(ROOT)
        self.assertEqual(baseline['counts'],{'passed':99,'partial':1})
        self.assertEqual([x['original_id'] for x in baseline['cases'] if x['verdict']=='partial'],['T091'])
        for case in baseline['cases']:
            self.assertEqual(len(case['evidence']),2)
            self.assertFalse(case['timing_comparable'])
            for entry in case['evidence']:
                self.assertNotIn('/Users/',(ROOT/'baselines/journey-4'/entry['path']).read_text())

    def test_saved_judge_record_must_cover_every_rule(self):
        case={'sessions':[{'id':'business','rubric':['one','two']}]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'sessions/business/semantic-input';folder.mkdir(parents=True)
            (folder/'texts.json').write_text(json.dumps({'outputs':{'result.md':'real text'}}))
            verdict={'session':'business','criteria':{'C01':'one','C02':'two'},'checks':[
                {'id':'C01','passed':True,'reason':'ok','evidence_file':'result.md','evidence_quote':'real'},
                {'id':'C02','passed':False,'reason':'incorrect calculation'}]}
            checks=saved_checks(case,[verdict],root)
            self.assertEqual([c['passed'] for c in checks],[True,False])
            with self.assertRaises(ValueError):saved_checks(case,[],root)
            verdict['checks'].pop()
            with self.assertRaises(ValueError):saved_checks(case,[verdict],root)

    def test_missing_current_results_cannot_become_historical_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            report=aggregate(root/'missing',root/'report','journey-4')
            self.assertEqual(report['total'],100)
            self.assertEqual(report['test_errors'],100)
            self.assertEqual(report['quality']['all_checks_passed'],0)
            comparison=report['historical_comparison']
            self.assertEqual(comparison['current_counts'],{'missing':100})
            self.assertEqual(comparison['historical_codex_counts'],{'passed':99,'partial':1})
            self.assertFalse(comparison['historical_counts_in_current_denominator'])
            self.assertTrue((root/'report/baseline/codex/T091/result.md').is_file())
            self.assertIn('部分通过',(root/'report/comparison.html').read_text())
            self.assertIn('historical_codex_verdict',(root/'report/summary.csv').read_text())

    def test_current_failed_checks_and_judge_error_remain_distinct(self):
        row={'workspace':'journey-4','case_id':'J4-01','title':'task','test_status':'completed',
             'quality':{'state':'checked','failed_checks':[{'rule':'numeric error'}]},'failures':[],'harness_errors':[]}
        comparison=build([row],ROOT)
        self.assertEqual(comparison['cases'][0]['current_state'],'failed')
        self.assertEqual(comparison['cases'][0]['codex_verdict'],'passed')
        row['test_status']='error'
        self.assertEqual(current_state(row),'error')
        self.assertIsNone(build([dict(row,workspace='journey-1')],ROOT))


if __name__=='__main__':
    unittest.main()
