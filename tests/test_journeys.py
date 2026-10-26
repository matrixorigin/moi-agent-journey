import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from journey.client import Client
from journey.a2a import final_reply
from journey.evidence import Evidence, safe_extract
from journey.report import aggregate, finish_case, scan
from journey.run_case import Execution


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
