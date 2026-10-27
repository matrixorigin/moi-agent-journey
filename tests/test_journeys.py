import contextlib
import datetime
import io
import json
import os
import struct
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.client import Client
from journey.a2a import final_reply
from journey.evidence import Evidence, safe_extract
from journey.report import aggregate, finish_case, scan
from journey.run_case import Execution
from journey.publish import prepare_publication
from journey.validate import validate
from judge.verify import PytestCapture, evo, pi_structural, require_evo_checks_executed
from judge.inventory import date_status
from judge.statement import normalize_row, pdf_records, record_difference
from judge.summary_sheet import source_cells, value_equal


@contextlib.contextmanager
def qa_fixture(mode):
    state = {"tasks": {}, "methods": [], "archived": False}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("answer.txt", "actual result")
    binary = stream.getvalue()
    if mode=='corrupt_archive':binary=b'PK\x03\x04invalid archive'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):pass

        def handle(self):
            # BaseHTTPRequestHandler calls handle before parsing a request.
            return super().handle()

        def respond(self):
            raw=self.rfile.read(int(self.headers.get("Content-Length",0)))
            body=json.loads(raw) if raw and self.headers.get("Content-Type","").startswith("application/json") else None
            state["methods"].append((self.command,self.path,body))
            code=200
            if self.path.startswith('/workspaces?'):
                response={"code":"OK","data":{"workspaces":[{"name":"journey-1","id":"ws","status":"ACTIVE"}]}}
            elif '/agents?' in self.path:
                response={"code":0,"data":{"items":[{"id":"momo","workspace_id":"system","status":"active","source_ref":"agent-zero-builder"}],"total":1}}
            elif self.path.endswith('/conversations'):
                state['conversation']=body['id'];response={"code":0,"data":{"id":body['id']}}
            elif self.command=='PATCH':
                state['archived']=True;response={"code":0,"data":{"status":"archived"}}
            elif self.path.endswith('/catalog-files/uploads'):
                response={"code":"OK","data":{"file_id":"input","original_name":"input.zip","catalog_file":{"workspace_id":"ws","volume_id":9,"file_id":"input"}}}
            elif self.path=='/agents/a2a':
                method=body['method']
                if method in {'message/send','message/stream'}:
                    tid='task-'+str(len(state['tasks'])+1)
                    terminal='failed' if mode=='agent_failure' and tid=='task-2' else 'completed'
                    state['tasks'][tid]={"state":terminal,"read":0}
                    if mode in {'timeout','async_cancel'}:state['client'].deadline=time.monotonic()+0.05
                else:tid=body['params']['id']
                task=state['tasks'][tid]
                if method=='tasks/cancel':
                    task['state']='working' if mode=='async_cancel' else 'canceled'
                    task['cancel_requested']=True
                if method=='tasks/get' and task.get('cancel_requested') and mode=='async_cancel':
                    task['read']+=1
                    if task['read']>=2:task['state']='canceled'
                phase=task['state']
                if mode in {'timeout','async_cancel'} and not task.get('cancel_requested'):phase='working'
                response={"jsonrpc":"2.0","id":body['id'],"result":{"kind":"task","id":tid,"contextId":state['conversation'],"status":{"state":phase,"message":{"role":"agent","parts":[{"kind":"text","text":"done"}]}}}}
                if method=='message/stream':
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    initial=json.loads(json.dumps(response));initial['result']['status']['state']='working'
                    if mode!='task_snapshot':
                        initial['result']={'kind':'status-update','taskId':tid,'contextId':state['conversation'],
                            'status':{'state':'working'},'final':False}
                    self.wfile.write(('id: 1\nevent: message\ndata: '+json.dumps(initial)+'\n\n').encode());self.wfile.flush()
                    if mode in {'timeout','async_cancel'}:
                        time.sleep(0.1)
                        self.wfile.write(b': heartbeat\n\n');self.wfile.flush();return
                    final={'jsonrpc':'2.0','id':body['id'],'result':{'kind':'status-update','taskId':tid,
                        'contextId':state['conversation'],'status':{'state':phase},'final':True}}
                    self.wfile.write(('id: 8\nevent: message\ndata: '+json.dumps(final)+'\n\n').encode());self.wfile.flush();return
            elif '/agent-runtime-tasks/' in self.path:
                tid=self.path.split('/agent-runtime-tasks/')[1].split('/')[0].split('?')[0]
                task=state['tasks'][tid]
                if '/events?' in self.path:
                    if mode=='event_failure':
                        code=500;response={"error":"secret_key"}
                    else:
                        after=int(self.path.split('after_seq=')[1])
                        events=[{"id":tid+'-'+str(seq),"task_id":tid,"seq":seq,"kind":kind,"payload":payload}
                            for seq,kind,payload in [(2,'thinking',{'text':'inspect inputs'}),(5,'tool_call',{'tool':'read'}),(8,'tool_result',{'result':'real bytes'})] if seq>after]
                        response={"code":0,"data":{"items":events,"total":len(events),"after_seq":after}}
                else:response={"code":0,"data":{"id":tid,"context_id":state['conversation'],"trace_id":"trace-"+tid,"event_cursor":8,"state":task['state']}}
            elif '/agent-artifacts?' in self.path:
                last=list(state['tasks'])[-1]
                response={"code":0,"data":{"items":[{"artifact_id":"artifact","version":1,"file_id":"output","file_name":"journey-output.zip","conversation_id":state['conversation'],"task_id":last}],"total":1}}
            elif self.path.endswith('/files/output/download'):
                self.send_response(200);self.end_headers();self.wfile.write(binary);return
            else:raise AssertionError(self.path)
            self.send_response(code);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        do_GET=do_POST=do_PATCH=respond

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        (root/'inputs').mkdir();(root/'inputs/a.txt').write_text('input')
        case={'id':'J1-01','title':'fixture','workspace':'journey-1','timeout_seconds':30,
            'source':{'benchmark':'fixture'},'assets':[{'path':'a.txt'}],
            'sessions':[{'id':'office','expected_files':['answer.txt'],
                         'turns':[{'phase':p,'prompt':p} for p in ['plan','execute','review']]}]}
        evidence=Evidence(root/'results',['secret_key'])
        client=Client('https://fixture.invalid','secret_key',timeout=30,observer=evidence.observe)
        client.base_url='http://127.0.0.1:'+str(server.server_port)
        state['client']=client
        try:yield Execution(client,case,evidence,root,'model'),state
        finally:server.shutdown();server.server_close();thread.join()


class JourneyTests(unittest.TestCase):
    def test_validator_failure_retains_the_specific_evaluator_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); evidence = Evidence(root/'results', [])
            evidence.write('result.json', {'status': 'awaiting_validation', 'sessions': [{'id': 'office'}]})
            (evidence.root/'sessions/office/outputs').mkdir(parents=True)
            work = root/'work'; work.mkdir()
            case = {'id': 'J1-14', 'workspace': 'journey-1'}
            def run(*args, **kwargs):
                path = evidence.root/'sessions/office/verification/verdict.json'
                path.write_text(json.dumps({'status': 'error', 'reason': 'upstream_oracle_incomplete'}))
                return SimpleNamespace(returncode=1, stdout='', stderr='')
            with patch('journey.validate.subprocess.run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'validator_error:upstream_oracle_incomplete'):
                    validate(case, work, evidence)

    def test_inventory_invalid_workbook_is_task_failure_not_evaluator_error(self):
        module = SimpleNamespace(evaluate=lambda _: {'criterion_results': [
            {'criterion_id': 'R001', 'evidence': 'invalid workbook', 'raw': {'passed': False}}]})
        with patch('judge.verify.module_at', return_value=module):
            checks, _ = evo({'id': 'J1-09'}, Path('/oracle'), Path('/inputs'), Path('/outputs'))
        self.assertEqual([(item['rule'], item['passed']) for item in checks], [('R001', False)])

    def test_ap_checker_reports_unexecuted_invoice_checks_as_framework_error(self):
        incomplete = {'criterion_results': [{'criterion_id': 'R012',
            'evidence': 'Excel 工作表应为 expected，实际 different'}]}
        with self.assertRaisesRegex(RuntimeError, 'upstream_oracle_incomplete'):
            require_evo_checks_executed({'id': 'J1-14'}, incomplete)
        require_evo_checks_executed({'id': 'J1-14'}, {'criterion_results': [
            {'criterion_id': 'R012', 'evidence': '工作表内容均已检查'}]})

    def test_summary_source_labels_handle_different_layouts_and_reject_ambiguity(self):
        class Sheet:
            title = 'child'
            def __init__(self, rows):
                self.cells = {(r, c): SimpleNamespace(row=r, column=c, value=value)
                              for r, values in enumerate(rows, 1)
                              for c, value in enumerate(values, 1)}
            def cell(self, row, column):
                return self.cells.get((row, column), SimpleNamespace(row=row, column=column, value=None))
            def __getitem__(self, row):
                return [cell for (r, _), cell in self.cells.items() if r == row]
            def __iter__(self):
                return iter([self[row] for row in range(1, max(r for r, _ in self.cells) + 1)])

        for total_label, total_value in [(['总重量合计', 19.0], [None, None]),
                                         ([19.0, '总重量合计'], [None, None]),
                                         ([None, '总重量合计'], [None, 19.0])]:
            sheet = Sheet([total_label, total_value, ['砼体积(m³)', None], [0.5, None]])
            volume, total = source_cells(sheet)
            self.assertEqual((volume.value, total.value), (0.5, 19.0))
        with self.assertRaisesRegex(ValueError, 'ambiguous_total_values'):
            source_cells(Sheet([['总重量合计', 1.0, 2.0], ['砼体积(m³)'], [0.5]]))
        self.assertTrue(value_equal(0.5, 0.5))
        self.assertFalse(value_equal(0.6, 0.5))

    def test_statement_records_reconcile_and_preserve_existing_transactions(self):
        statement = '''01 Dec. Balance forward $100.00
01 Dec. Insurance 20.00 80.00
02 Dec. Sales 50.00 130.00
02 Dec. Closing totals 20.00 50.00 130.00'''
        records, _ = pdf_records(statement)
        original = normalize_row(['Mar 22', 'Old transaction', '', '$10.00', '$70.00'])
        rows = [normalize_row(['01 Dec', 'Insurance', '-$20.00', '', '$80.00']),
                normalize_row(['02 Dec', 'Sales', '', '$50.00', '$130.00']), original]
        self.assertEqual(record_difference(records + [original], rows), ({}, {}))
        missing, unexpected = record_difference(records + [original], rows[:-1])
        self.assertEqual(sum(missing.values()), 1)
        self.assertFalse(unexpected)
        rows[0] = normalize_row(['01 Dec', 'Insurance', '-$21.00', '', '$80.00'])
        missing, unexpected = record_difference(records + [original], rows)
        self.assertEqual((sum(missing.values()), sum(unexpected.values())), (1, 1))
        with self.assertRaisesRegex(ValueError, 'pdf_transaction_does_not_reconcile'):
            pdf_records(statement.replace('20.00 80.00', '20.00 81.00'))

    def test_inventory_dates_accept_blank_no_order_dates_but_reject_wrong_dates(self):
        expected={1:{'needs_reorder':False,'reorder_date':None},
                  2:{'needs_reorder':True,'reorder_date':datetime.date(2026,3,8)}}
        parse=lambda value: value if isinstance(value,datetime.date) else None
        for blank in [None,'','N/A']:
            records=[{'Product_ID':1,'Reorder_Date':blank},
                     {'Product_ID':2,'Reorder_Date':datetime.date(2026,3,8)}]
            self.assertTrue(date_status(records,expected,parse,int)[0])
        records[1]['Reorder_Date']=datetime.date(2026,3,9)
        self.assertEqual(date_status(records,expected,parse,int)[1][0]['reason'],'wrong_reorder_date')
        records[0]['Reorder_Date']='unexpected'
        self.assertIn('unexpected_reorder_date',[e['reason'] for e in date_status(records,expected,parse,int)[1]])

    def test_pytest_inapplicable_checks_are_reported_without_failing_the_case(self):
        capture=PytestCapture()
        capture.allowed_skips.add('pivot_without_column')
        capture.record(SimpleNamespace(nodeid='pivot_without_column',when='call',
            skipped=True,failed=False,passed=False,longrepr='not a matrix pivot',outcome='skipped'))
        capture.record(SimpleNamespace(nodeid='source_data',when='call',
            skipped=False,failed=False,passed=True,longrepr=None,outcome='passed'))
        checks,raw=capture.result(0)
        self.assertEqual([item['rule'] for item in checks],['source_data'])
        self.assertEqual([item['rule'] for item in raw['skipped']],['pivot_without_column'])

    def test_unexpected_skip_and_empty_checks_are_framework_errors(self):
        capture=PytestCapture()
        capture.record(SimpleNamespace(nodeid='missing_dependency',when='setup',
            skipped=True,failed=False,passed=False,longrepr='dependency missing',outcome='skipped'))
        with self.assertRaisesRegex(RuntimeError,'unexpected_skipped_checks'):
            capture.result(0)
        with self.assertRaisesRegex(RuntimeError,'executed_no_checks'):
            PytestCapture().result(0)

    def test_unreadable_archive_is_isolated_without_losing_trace_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case=root/'results/J1-16';case.mkdir(parents=True)
            (case/'result.json').write_text(json.dumps({'case_id':'J1-16','status':'failed','harness_status':'ok'}))
            trace='{"task_id":"task-16","seq":7,"kind":"tool_result","payload":{"result":"bad ZIP"}}\n'
            (case/'events.jsonl').write_text(trace)
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as bundle:bundle.writestr('paper.pdf','public content')
            data=bytearray(stream.getvalue());end=data.rfind(b'PK\x05\x06')
            offset=struct.unpack_from('<L',data,end+16)[0]
            struct.pack_into('<L',data,end+16,offset+188743680)
            payload=case/'artifacts/context/file/journey-output.zip';payload.parent.mkdir(parents=True);payload.write_bytes(data)
            for suffix in ['.01','.002','.part02','.PART03']:
                payload.with_name(payload.name+suffix).write_bytes(b'opaque middle volume')
            rejected=prepare_publication(root/'results',root/'public',[])
            self.assertEqual((root/'public/J1-16/events.jsonl').read_text(),trace)
            self.assertFalse((root/'public/J1-16/artifacts/context/file/journey-output.zip').exists())
            result=json.loads((root/'public/J1-16/result.json').read_text())
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['publication']['status'],'partial')
            self.assertTrue(any(r['reason'].startswith('archive_not_inspectable:') for r in rejected))
            self.assertEqual(sum(r['reason']=='split_archive_volume_not_inspectable' for r in rejected),4)

    def test_publication_redacts_records_and_withholds_secret_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case=root/'results/J1-01';case.mkdir(parents=True)
            (case/'result.json').write_text(json.dumps({'case_id':'J1-01','status':'failed'}))
            (case/'events.jsonl').write_text('{"text":"private-qa-key"}\n')
            payload=case/'sessions/office/outputs/config.txt';payload.parent.mkdir(parents=True);payload.write_text('private-qa-key')
            rejected=prepare_publication(root/'results',root/'public',['private-qa-key'])
            self.assertIn('[REDACTED]',(root/'public/J1-01/events.jsonl').read_text())
            self.assertFalse((root/'public/J1-01/sessions/office/outputs/config.txt').exists())
            self.assertEqual(rejected[0]['reason'],'credential_detected_in_artifact')
            self.assertTrue(all(b'private-qa-key' not in p.read_bytes() for p in (root/'public').rglob('*') if p.is_file()))

    def test_procurement_checks_selected_items_instead_of_words_in_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            data={key:'' for key in ['primary_procurement_move','next_wave_gel_basics','conditional_backup_lab_support',
                'deferred_route_tied_confirmation','confirmation_action','fallback_if_no_reply','draft_corrections']}
            names=['Target-gene primers','Sealing film','RNase-free filter tips','CCK-8 reagent','96-well cell plates','Carbomer 940','Triethanolamine','Propylene Glycol']
            data['immediate_slots']=[{'item':name,'why_earns_immediate_slot':'FBS, Trypsin and 0.22 um filters are deferred.'} for name in names]
            file=root/'merged_procurement_plan.json';file.write_text(json.dumps(data))
            checks,_=pi_structural({'id':'J1-17'},root,'pharmacist_task_019')
            self.assertTrue(all(c['passed'] for c in checks))
            data['immediate_slots'][-1]['item']='FBS';file.write_text(json.dumps(data))
            checks,_=pi_structural({'id':'J1-17'},root,'pharmacist_task_019')
            self.assertFalse(all(c['passed'] for c in checks))

    def test_final_reply_excludes_timeline_text_from_structured_answer(self):
        answer='{"checks":[{"id":"C01","passed":true}]}'
        task={'status':{'state':'completed','message':{'role':'agent','parts':[
            {'kind':'data','data':{'type':'matrixflow.explore.llm_call','status':'started'}},
            {'kind':'text','text':'Interim progress while using tools'},
            {'kind':'text','text':answer},
            {'kind':'data','data':{'type':'matrixflow.explore.llm_call','status':'completed'}},
            {'kind':'text','text':answer}]}}}
        self.assertEqual(json.loads(final_reply(task)),{'checks':[{'id':'C01','passed':True}]})

    def test_multi_turn_real_wire_capture_and_download(self):
        with qa_fixture('success') as (execution,state):
            result=execution.run()
            self.assertEqual(result['status'],'awaiting_validation')
            self.assertTrue(result['capture_complete'])
            self.assertEqual(result['harness_errors'],[])
            self.assertEqual({t['phase'] for t in result['tasks']},{'office:plan','office:execute','office:review'})
            events=[json.loads(line) for line in (execution.evidence.root/'events.jsonl').read_text().splitlines()]
            self.assertEqual({(e['task_id'],e['seq']) for e in events},
                {(tid,seq) for tid in ['task-1','task-2','task-3'] for seq in [2,5,8]})
            self.assertEqual(len(events),len({(e['task_id'],e['seq']) for e in events}))
            self.assertEqual({t['trace_id'] for t in result['tasks']},{'trace-task-1','trace-task-2','trace-task-3'})
            self.assertEqual((execution.evidence.root/'sessions/office/outputs/answer.txt').read_text(),'actual result')
            self.assertTrue(state['archived'])
            self.assertNotIn('secret_key',(execution.evidence.root/'http.jsonl').read_text())

    def test_agent_failure_preserves_prior_and_failed_task_evidence(self):
        with qa_fixture('agent_failure') as (execution,state):
            result=execution.run()
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['failures'][0]['reason'],'agent_failed')
            self.assertTrue(result['capture_complete'])
            self.assertEqual(result['harness_errors'],[])
            self.assertEqual([t['state'] for t in result['tasks']],['completed','failed'])
            self.assertTrue((execution.evidence.root/'artifacts'/state['conversation']/'inventory.json').is_file())

    def test_initial_task_snapshot_is_also_a_valid_a2a_stream(self):
        with qa_fixture('task_snapshot') as (execution,state):
            result=execution.run()
            self.assertEqual(result['status'],'awaiting_validation')
            self.assertTrue(result['capture_complete'])

    def test_task_latency_does_not_include_semantic_judge_latency(self):
        with qa_fixture('success') as (execution,state):
            execution.case['source']['benchmark']='π-Bench'
            with patch.object(execution,'judge_pi',side_effect=lambda:time.sleep(0.03)):
                result=execution.run()
            self.assertEqual(result['status'],'awaiting_validation')
            self.assertGreaterEqual(result['semantic_judge_seconds'],0.02)
            self.assertGreater(result['qa_seconds'],result['task_seconds'])

    def test_deadline_cancels_and_keeps_the_failure(self):
        with qa_fixture('timeout') as (execution,state):
            result=execution.run()
            self.assertEqual(result['status'],'timeout')
            self.assertEqual(result['tasks'][0]['state'],'canceled')
            self.assertTrue(result['capture_complete'])
            self.assertTrue(state['archived'])
            sends=[b for _,p,b in state['methods'] if p=='/agents/a2a' and b['method']=='message/stream']
            self.assertEqual(len(sends),1)

    def test_evidence_failure_is_a_harness_error(self):
        with qa_fixture('event_failure') as (execution,state):
            result=execution.run()
            self.assertFalse(result['capture_complete'])
            self.assertTrue(result['harness_errors'])

    def test_asynchronous_cancellation_is_confirmed_before_archive(self):
        with qa_fixture('async_cancel') as (execution,state):
            result=execution.run()
            self.assertEqual(result['status'],'timeout')
            self.assertEqual(result['tasks'][0]['state'],'canceled')
            self.assertTrue(result['capture_complete'])
            self.assertEqual(result['harness_errors'],[])
            self.assertTrue(state['archived'])

    def test_corrupt_deliverable_is_a_task_failure_with_evidence(self):
        with qa_fixture('corrupt_archive') as (execution,state):
            result=execution.run()
            self.assertEqual(result['status'],'failed')
            self.assertTrue(result['failures'][0]['reason'].startswith('invalid_output_archive'))
            self.assertEqual(result['harness_errors'],[])
            self.assertTrue(result['capture_complete'])

    def test_nested_credentials_are_blocked_and_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            archive=root/'output.zip'
            with zipfile.ZipFile(archive,'w') as bundle:bundle.writestr('output.txt','private-access-token')
            with self.assertRaisesRegex(ValueError,'credential_detected'):
                scan(root,['private-access-token'])
            with zipfile.ZipFile(archive,'w') as bundle:bundle.writestr('../escape.txt','bad')
            with self.assertRaisesRegex(ValueError,'unsafe_archive_member'):
                safe_extract(archive,root/'outputs')
            self.assertFalse((root/'escape.txt').exists())

    def test_missing_case_cannot_inflate_success_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';destination=root/'bundle'
            config={'journeys':[{'workspace':'journey-1','concurrency':3,'cases':['J1-01','J1-02']},
                               {'workspace':'journey-2','concurrency':3,'cases':[]}]}
            (root/'suites.json').write_text(json.dumps(config))
            for cid,status in [('J1-01','passed'),('J1-02','failed')]:
                evidence=Evidence(source/cid)
                evidence.write('result.json',{'case_id':cid,'workspace':'journey-1','title':cid,'status':status,
                    'harness_status':'ok','tasks':[],'failures':[],'harness_errors':[]})
            with patch('journey.report.ROOT',root):
                report=aggregate(source,destination)
                self.assertEqual(report['success_rate'],0.5)
                self.assertEqual(report['journeys'][1]['status'],'empty')
                (source/'J1-02/result.json').unlink()
                report=aggregate(source,destination)
                self.assertEqual(report['success_rate'],0.5)
                self.assertEqual(report['harness_errors'],1)
                self.assertEqual(report['cases'][1]['harness_errors'][0]['reason'],'case_artifact_missing')

    def test_artifact_scan_never_follows_links_to_runner_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence=root/'evidence';evidence.mkdir()
            private=root/'runner-private.txt';private.write_text('runner-only-data')
            (evidence/'leak.txt').symlink_to(private)
            with self.assertRaisesRegex(ValueError,'artifact_symlink_not_allowed'):
                scan(evidence,[])

    def test_killed_case_is_not_reported_as_a_product_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Evidence(Path(directory)/'J1-01')
            evidence.write('result.json',{'case_id':'J1-01','status':'running','tasks':[],
                'failures':[],'harness_errors':[],'stage':'execute'})
            report=finish_case('J1-01',directory)
            self.assertEqual(report['status'],'error')
            self.assertEqual(report['harness_status'],'error')


if __name__=='__main__':unittest.main()
