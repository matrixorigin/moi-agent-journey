import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.client import Client, Failure
from journey.catalog import load_case
from journey.a2a import final_reply
from journey.diagnostics import analyze_events
from journey.evidence import Evidence, safe_extract
from journey.outcomes import assess
from journey.report import aggregate, case_summary, finish_case, main as report_main, scan
from journey.run_case import Execution
from journey.publish import prepare_publication
from journey.validate import lineage_checks, validate
from judge.verify import PytestCapture, evo, pi_structural, render_outputs
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
    @staticmethod
    def finished_result(case_id, *, task_state='completed', test_status='completed'):
        agent_state = {'completed':'completed', 'abnormal':'abnormal', 'timeout':'timeout',
                       'not_delivered':'completed', 'unknown':'unknown'}[task_state]
        return {'case_id':case_id, 'workspace':'journey-1', 'title':case_id, 'test_status':test_status,
                'task':{'state':task_state}, 'agent':{'state':agent_state,'completed_turns':1,'expected_turns':1},
                'delivery':{'state':'delivered' if task_state=='completed' else 'unknown',
                            'completed_sessions':1 if task_state=='completed' else 0,'expected_sessions':1},
                'quality':{'state':'checked','passed_checks':1,'total_checks':1,'failed_checks':[]},
                'tools':{'state':'available','complete':True,
                    'metrics':{'calls':2,'succeeded':1,'failed':1,'skipped':0,'unknown':0,
                               'tool_rounds':1,'round_unmapped_calls':0,'unidentified_events':0,'success_rate':0.5,
                               'by_tool':[{'tool':'bash','calls':2,'succeeded':1,'failed':1,'skipped':0,'unknown':0,'success_rate':0.5}]},
                    'errors':[],'judge_errors':[],'runtime_sessions':{}},
                'tasks':[], 'failures':[], 'harness_errors':[], 'task_seconds':10}

    def test_lineage_accepts_real_archive_paths_and_rejects_duplicates_or_wrong_data(self):
        sessions = [{'id':'session-a', 'outputs':{'one.csv':'hash-one'}},
                    {'id':'session-b', 'outputs':{'nested/two.csv':'hash-two'}}]
        sources = [{'session_id':'session-a', 'path':'one.csv', 'sha256':'hash-one'},
                   {'session_id':'session-b', 'path':'nested/two.csv', 'sha256':'hash-two'}]
        self.assertTrue(lineage_checks({'sources':sources}, sessions, 'final')[0]['passed'])
        actual_paths = [{**entry, 'path':'prior_outputs/'+entry['session_id']+'/'+entry['path']} for entry in sources]
        self.assertTrue(lineage_checks({'sources':actual_paths}, sessions, 'final')[0]['passed'])
        dotted = [dict(entry) for entry in actual_paths]; dotted[0]['path']='./'+dotted[0]['path']
        self.assertTrue(lineage_checks({'sources':dotted}, sessions, 'final')[0]['passed'])
        self.assertFalse(lineage_checks({'sources':actual_paths+[actual_paths[0]]}, sessions, 'final')[0]['passed'])
        changed = [dict(entry) for entry in actual_paths]; changed[0]['sha256']='wrong'
        self.assertFalse(lineage_checks({'sources':changed}, sessions, 'final')[0]['passed'])
        changed = [dict(entry) for entry in actual_paths]; changed[0]['path']='prior_outputs/session-b/one.csv'
        self.assertFalse(lineage_checks({'sources':changed}, sessions, 'final')[0]['passed'])
        changed = [dict(entry) for entry in actual_paths]; changed[0]['path']='prior_outputs/session-a/../one.csv'
        self.assertFalse(lineage_checks({'sources':changed}, sessions, 'final')[0]['passed'])
        self.assertFalse(lineage_checks(None, sessions, 'final')[0]['passed'])

    def test_office_tool_crash_is_a_harness_error_not_a_failed_deliverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); outputs=root/'outputs'; outputs.mkdir()
            (outputs/'document.docx').write_bytes(b'fixture')
            with patch('judge.verify.subprocess.run', return_value=SimpleNamespace(
                    returncode=-11, stdout='', stderr='process crashed')):
                with self.assertRaisesRegex(RuntimeError, 'office_render_process_failed:exit=-11'):
                    render_outputs(outputs, root/'rendered')
            with patch('judge.verify.subprocess.run', return_value=SimpleNamespace(
                    returncode=0, stdout='unsupported file', stderr='')):
                with self.assertRaisesRegex(RuntimeError, 'office_render_no_output'):
                    render_outputs(outputs, root/'rendered')

    def test_observed_tool_errors_record_evidence_without_changing_case_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); evidence=Evidence(root/'J1-01', ['private-token'])
            evidence.write('result.json', {'case_id':'J1-01', 'status':'failed',
                'tasks':[{'task_id':'task-1','phase':'office:plan','event_count':6}],
                'failures':[], 'harness_errors':[], 'capture_complete':True})
            events=[
                {'task_id':'task-1','seq':0,'kind':'lifecycle','payload':{'type':'tool_transport_failed',
                    'tool':'bash','call_id':'a','error_kind':'stream_transport','error':'connection failed'}},
                {'task_id':'task-1','seq':1,'kind':'tool_result','payload':{'tool':'bash','call_id':'a',
                    'arguments':{'command':'run --token private-token','OPENAI_API_KEY':'example-key'},
                    'success':False,'status':'failed','error_kind':'stream_transport','error':'TOOL_EXECUTION_FAILED',
                    'output':'connection failed: private-token'}},
                {'task_id':'task-1','seq':2,'kind':'tool_result','payload':{'tool':'bash','call_id':'b',
                    'success':True,'output':{'output':{'stdout':'prefix\n{"status":413,"code":"upload_denied","request_id":"r1"}\n',
                                                       'stderr':'not JSON'}}}},
                {'task_id':'task-1','seq':3,'kind':'tool_result','payload':{'tool':'bash','call_id':'c','success':True,
                    'output':{'output':{'stdout':'{"status":200,"code":"OK"}'}}}},
                {'task_id':'task-1','seq':4,'kind':'tool_result','payload':{'tool':'search','call_id':'d','success':True,
                    'output':{'tool_id':'web_search','output':{'ok':False,'error_code':'QUOTA_EXCEEDED',
                              'provider':'search_provider','message':'Service quota exhausted'}}}},
                {'task_id':'task-1','seq':5,'kind':'tool_result','payload':{'tool':'(surgically_removed)',
                    'call_id':'e','success':False,'status':'skipped','error':'not_executed'}},
            ]
            for event in events:evidence.append('events.jsonl',event)
            with patch.dict(os.environ, {'GITHUB_SHA':'test-commit'}):
                report=finish_case('J1-01',root)
            self.assertEqual(report['test_status'],'completed')
            self.assertEqual(report['workflow_commit'], 'test-commit')
            specification=json.dumps(load_case('J1-01'),ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            self.assertEqual(report['case_spec_sha256'], hashlib.sha256(specification).hexdigest())
            metrics=report['tools']['metrics']; self.assertEqual(
                {key:metrics[key] for key in ('calls','succeeded','failed','skipped','unknown')},
                {'calls':5,'succeeded':1,'failed':2,'skipped':1,'unknown':1})
            errors={item['call_id']:item for item in report['tools']['errors']}
            self.assertEqual(len(errors['a']['errors']),2)
            self.assertEqual(errors['b']['errors'][0]['request_id'],'r1')
            self.assertEqual(errors['d']['errors'][0]['code'],'QUOTA_EXCEEDED')
            self.assertNotIn('private-token',(evidence.root/'result.json').read_text())
            self.assertNotIn('example-key',(evidence.root/'result.json').read_text())
            self.assertIn('bash',(evidence.root/'case-summary.md').read_text())
            self.assertIn('connection failed',(evidence.root/'case-summary.md').read_text())

    def test_invalid_event_log_is_a_framework_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); evidence=Evidence(root/'J1-01', [])
            evidence.write('result.json', {'case_id':'J1-01', 'status':'failed', 'tasks':[{'event_count':1}],
                'failures':[], 'harness_errors':[], 'capture_complete':True})
            (evidence.root/'events.jsonl').write_text('not-json\n')
            report=finish_case('J1-01',root)
            self.assertEqual(report['test_status'],'error')
            self.assertTrue(report['harness_errors'][0]['reason'].startswith('invalid_event_log:'))

    def test_tool_metrics_deduplicate_and_exclude_judge_calls_and_unknowns(self):
        events = [
            {'task_id':'main','seq':1,'kind':'lifecycle','payload':{'type':'session_info',
                'session_id':'[redacted]','external_session_id':'[redacted]'}},
            {'task_id':'main','seq':2,'kind':'lifecycle','payload':{'type':'llm_finished','call_id':'round-1'}},
            {'task_id':'main','seq':3,'kind':'tool_call','payload':{'call_id':'good','tool_id':'bash','arguments':{'command':'ok'}}},
            {'task_id':'main','seq':4,'kind':'tool_call','payload':{'call_id':'bad','tool_id':'bash','arguments':{'command':'bad'}}},
            {'task_id':'main','seq':5,'kind':'tool_result','payload':{'call_id':'good','success':True,
                'output':{'output':{'exit_code':0,'stdout':'ok'}}}},
            {'task_id':'main','seq':6,'kind':'lifecycle','payload':{'type':'tool_transport_failed','call_id':'bad','error':'lost'}},
            {'task_id':'main','seq':7,'kind':'tool_result','payload':{'call_id':'bad','success':False,
                'status':'failed','error':'TOOL_FAILED','output':'lost'}},
            {'task_id':'main','seq':8,'kind':'lifecycle','payload':{'type':'llm_finished','call_id':'round-2'}},
            {'task_id':'main','seq':9,'kind':'tool_call','payload':{'call_id':'skipped','tool_id':'bash'}},
            {'task_id':'main','seq':10,'kind':'tool_result','payload':{'call_id':'skipped','success':False,'status':'skipped'}},
            {'task_id':'main','seq':11,'kind':'tool_call','payload':{'call_id':'partial','tool_id':'bash',
                'arguments':{'command':'upload'}}},
            {'task_id':'main','seq':12,'kind':'tool_result','payload':{'call_id':'partial','success':True,
                'output':{'output':{'exit_code':0,'stdout':'Error: [upload_denied] Request Entity Too Large'}}}},
            {'task_id':'main','seq':13,'kind':'tool_call','payload':{'call_id':'pending','tool_id':'bash'}},
            {'task_id':'judge','seq':1,'kind':'tool_call','payload':{'call_id':'judge-call','tool_id':'search'}},
            {'task_id':'judge','seq':2,'kind':'tool_result','payload':{'call_id':'judge-call','success':True}},
            {'task_id':'judge','seq':3,'kind':'tool_call','payload':{'call_id':'judge-bad','tool_id':'search',
                'arguments':{'query':'source'}}},
            {'task_id':'judge','seq':4,'kind':'tool_result','payload':{'call_id':'judge-bad','success':False,
                'status':'failed','error':'quota','output':'quota exhausted'}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in events))
            result=analyze_events(path,[{'task_id':'main','phase':'office:execute','event_count':13},
                                       {'task_id':'judge','phase':'judge:office','event_count':4}])
        m=result['metrics']
        self.assertEqual({k:m[k] for k in ('calls','succeeded','failed','skipped','unknown','tool_rounds')},
                         {'calls':5,'succeeded':1,'failed':1,'skipped':1,'unknown':2,'tool_rounds':2})
        self.assertEqual(m['success_rate'],0.5)
        self.assertEqual((m['by_tool'][0]['tool'],m['by_tool'][0]['success_rate']),('bash',0.5))
        errors={item['call_id']:item for item in result['errors']}
        self.assertEqual(len(errors['bad']['errors']),2)
        self.assertEqual(errors['bad']['arguments'],{'command':'bad'})
        self.assertEqual(errors['partial']['outcome'],'unknown')
        self.assertEqual(errors['partial']['errors'][0]['code'],'upload_denied')
        self.assertTrue(result['runtime_sessions']['main']['redacted'])
        self.assertEqual(result['judge_errors'][0]['arguments'],{'query':'source'})
        self.assertEqual(result['judge_errors'][0]['errors'][0]['code'],'quota')

    def test_tool_parameters_redact_secrets_but_keep_diagnostic_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Evidence(Path(directory),['actual-private-pat'])
            args={'OPENAI_API_KEY':'not-a-known-token', 'nested':{'clientSecret':'hidden-secret'},
                  'command':'UPLOAD_TOKEN=third-party-token tool --api-key other-key --limit 25 actual-private-pat',
                  'header':'Authorization: Basic encoded-credential'}
            cleaned=evidence.clean(args)
            self.assertEqual(cleaned['OPENAI_API_KEY'],'[REDACTED]')
            self.assertEqual(cleaned['nested']['clientSecret'],'[REDACTED]')
            for secret in ('third-party-token','other-key','actual-private-pat'):
                self.assertNotIn(secret,cleaned['command'])
            self.assertIn('--limit 25',cleaned['command'])
            self.assertNotIn('encoded-credential',cleaned['header'])

    def test_tool_rejection_reports_the_detailed_validation_error(self):
        events=[{'task_id':'t','seq':1,'kind':'tool_call','payload':{'call_id':'c','tool_id':'search',
                    'arguments':{'limit':50}}},
                {'task_id':'t','seq':2,'kind':'tool_result','payload':{'call_id':'c','success':False,
                    'status':'rejected','error':'TOOL_EXECUTION_FAILED','error_kind':'tool_invalid_args',
                    'output':{'error':'field limit must be at most 10','status':'rejected'}}}]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in events))
            analysis=analyze_events(path,[{'task_id':'t','phase':'office:execute','event_count':2}])
        self.assertEqual(analysis['metrics']['failed'],1)
        self.assertEqual(analysis['errors'][0]['arguments'],{'limit':50})
        self.assertEqual(analysis['errors'][0]['errors'][0]['detail'],'field limit must be at most 10')

    def test_missing_call_ids_are_not_double_counted_or_reported_as_success(self):
        events=[{'task_id':'t','seq':1,'kind':'tool_call','payload':{'tool_id':'bash'}},
                {'task_id':'t','seq':2,'kind':'tool_result','payload':{'tool_id':'bash','success':True}},
                {'task_id':'t','seq':3,'kind':'tool_result','payload':{'tool_id':'bash','success':False,
                    'status':'failed','error':'tool_failed'}}]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in events))
            analysis=analyze_events(path,[{'task_id':'t','phase':'office:execute','event_count':3}])
            metrics=analysis['metrics']
        self.assertEqual(metrics['calls'],0)
        self.assertEqual(metrics['unidentified_events'],3)
        self.assertIsNone(metrics['success_rate'])
        self.assertEqual(analysis['errors'][0]['errors'][0]['code'],'tool_failed')

    def test_delivery_and_quality_are_independent_of_request_completion(self):
        case=load_case('J1-01')
        tasks=[{'task_id':str(i),'phase':'office:'+turn['phase'],'state':'completed'}
               for i,turn in enumerate(case['sessions'][0]['turns'])]
        result={'tasks':tasks,'sessions':[],'harness_errors':[],
                'failures':[{'reason':'final_output_bundle_not_unique','details':{'candidate_count':0}}]}
        task,agent,delivery,quality=assess(case,result,'failed')
        self.assertEqual((task['state'],agent['state'],delivery['state'],quality['state']),
                         ('not_delivered','completed','not_delivered','unavailable'))
        result['failures'][0]['details']['candidate_count']=2
        task,_,delivery,_=assess(case,result,'failed')
        self.assertEqual((task['state'],delivery['state']),('unknown','ambiguous'))
        result['sessions']=[{'id':'office','outputs':{'contract-accepted.docx':'digest'}}]
        result['validation']={'checks':[{'rule':'content','passed':False}]}
        task,_,delivery,quality=assess(case,result,'failed')
        self.assertEqual((task['state'],delivery['state'],quality['state'],quality['passed_checks']),
                         ('completed','delivered','checked',0))
        result['harness_errors']=[{'stage':'validation','reason':'validator_crashed'}]
        _,_,_,quality=assess(case,result,'error')
        self.assertEqual(quality['state'],'partial')
        result['sessions']=[]
        result['tasks']=tasks[:1]
        result['failures']=[{'reason':'agent_failed'}]
        task,agent,delivery,_=assess(case,result,'failed')
        self.assertEqual((task['state'],agent['state'],delivery['state']),
                         ('abnormal','abnormal','unknown'))
        result['failures']=[]
        result['tasks']=[{**tasks[0], 'state':'failed'}]
        task,agent,_,_=assess(case,result,'error')
        self.assertEqual((task['state'],agent['state']),('abnormal','abnormal'))

    def test_quality_report_states_when_the_checker_gave_no_reason(self):
        result=self.finished_result('J1-02')
        result['quality']={'state':'checked', 'passed_checks':0, 'total_checks':1,
                           'failed_checks':[{'session':'office','rule':'字体格式','detail':''}]}
        self.assertIn('验收器未提供具体原因', case_summary(result))

    def test_case_report_keeps_conversation_without_an_admitted_task(self):
        result=self.finished_result('J1-01')
        result['conversations']=[{'id':'conversation-before-send','purpose':'office'}]
        markdown=case_summary(result)
        self.assertIn('conversation-before-send',markdown)
        self.assertIn('未获取',markdown)

    def test_validator_failure_retains_the_specific_evaluator_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); evidence = Evidence(root/'results', [])
            evidence.write('result.json', {'status': 'awaiting_validation', 'sessions': [{'id': 'office'}]})
            (evidence.root/'sessions/office/outputs').mkdir(parents=True)
            work = root/'work'; work.mkdir()
            case = {'id': 'J1-14', 'workspace': 'journey-1'}
            def run(*args, **kwargs):
                self.assertIn('--init', args[0])
                path = evidence.root/'sessions/office/verification/verdict.json'
                path.write_text(json.dumps({'status': 'error', 'reason': 'upstream_oracle_incomplete'}))
                return SimpleNamespace(returncode=1, stdout='', stderr='')
            with patch('journey.validate.subprocess.run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'validator_error:upstream_oracle_incomplete'):
                    validate(case, work, evidence)

    def test_validator_error_keeps_checks_completed_in_previous_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); evidence=Evidence(root/'results', [])
            evidence.write('result.json', {'status':'awaiting_validation', 'sessions':[{'id':'first'}, {'id':'second'}]})
            for session in ('first', 'second'):
                (evidence.root/'sessions'/session/'outputs').mkdir(parents=True)
            work=root/'work'; work.mkdir()
            case={'id':'J1-17', 'workspace':'journey-1'}
            def run(command, **kwargs):
                session=command[command.index('--session')+1]
                verdict=({'status':'completed', 'checks':[{'rule':'first check','passed':True}]} if session=='first'
                         else {'status':'error', 'reason':'checker_crashed'})
                (evidence.root/'sessions'/session/'verification/verdict.json').write_text(json.dumps(verdict))
                return SimpleNamespace(returncode=0 if session=='first' else 1, stdout='', stderr='')
            with patch('journey.validate.subprocess.run', side_effect=run):
                with self.assertRaisesRegex(RuntimeError, 'validator_error:checker_crashed'):
                    validate(case, work, evidence)
            result=json.loads((evidence.root/'result.json').read_text())
            self.assertEqual(result['validation']['checks'],
                             [{'session':'first','rule':'first check','passed':True}])

    def test_inventory_invalid_workbook_is_task_failure_not_evaluator_error(self):
        module = SimpleNamespace(evaluate=lambda _: {'criterion_results': [
            {'criterion_id': 'R001', 'evidence': 'invalid workbook', 'raw': {'passed': False}}]})
        with patch('judge.verify.module_at', return_value=module):
            checks, _ = evo({'id': 'J1-09'}, Path('/oracle'), Path('/inputs'), Path('/outputs'))
        self.assertEqual([(item['rule'], item['passed']) for item in checks], [('R001', False)])

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
        records, _, opening = pdf_records(statement)
        self.assertEqual((opening[0], str(opening[1])), ((12, 1), '100.00'))
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
        capture=PytestCapture()
        capture.record(SimpleNamespace(nodeid='fixture_failure',when='setup',
            skipped=False,failed=True,passed=False,longrepr='fixture crashed',outcome='failed'))
        with self.assertRaisesRegex(RuntimeError,'oracle_setup_or_teardown_error'):
            capture.result(1)

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

    def test_invalid_semantic_judge_verdict_is_not_an_agent_failure(self):
        for reason in ('invalid_semantic_judge_verdict:invalid_json', 'case_timeout'):
            with self.subTest(reason=reason), qa_fixture('success') as (execution,state):
                execution.case['source']['benchmark']='π-Bench'
                with patch.object(execution,'judge_pi',side_effect=Failure(reason)):
                    result=execution.run()
                self.assertEqual(result['status'],'error')
                self.assertEqual(result['failures'],[])
                self.assertEqual(result['harness_errors'][0]['stage'],'semantic_judge')
                self.assertEqual(result['harness_errors'][0]['reason'],reason)
                self.assertTrue(result['capture_complete'])
                self.assertTrue(state['archived'])

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

    def test_unstarted_agent_and_unavailable_artifact_are_not_agent_failures(self):
        with qa_fixture('success') as (execution, _):
            with patch('journey.run_case.discover', side_effect=Failure('http_error', 401)):
                result=execution.run()
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['failures'], [])
            self.assertEqual(result['harness_errors'][0]['reason'], 'http_error')
            task, agent, delivery, _ = assess(execution.case, result, result['status'])
            self.assertEqual((task['state'], agent['state'], delivery['state']),
                             ('unknown', 'unknown', 'unknown'))

        with qa_fixture('success') as (execution, _):
            collect = execution.collect_artifacts
            def fail_download(cid, entry=None):
                if entry is not None:
                    raise Failure('transport_error')
                return collect(cid, entry)
            with patch.object(execution, 'collect_artifacts', side_effect=fail_download):
                result=execution.run()
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['harness_errors'][0]['reason'], 'transport_error')
            task, agent, delivery, _ = assess(execution.case, result, result['status'])
            self.assertEqual((task['state'], agent['state'], delivery['state']),
                             ('unknown', 'completed', 'unknown'))

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

    def test_missing_case_is_unknown_and_a_framework_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';destination=root/'bundle'
            config={'journeys':[{'workspace':'journey-1','concurrency':3,'cases':['J1-01','J1-02']},
                               {'workspace':'journey-2','concurrency':3,'cases':[]}]}
            (root/'suites.json').write_text(json.dumps(config))
            for cid,state in [('J1-01','completed'),('J1-02','abnormal')]:
                evidence=Evidence(source/cid)
                evidence.write('result.json',self.finished_result(cid,task_state=state))
            with patch('journey.report.ROOT',root):
                report=aggregate(source,destination)
                self.assertEqual(report['task']['completed'],1)
                self.assertEqual(report['task']['abnormal'],1)
                self.assertEqual(report['confirmed_completion_rate'],0.5)
                self.assertEqual(report['tools']['success_rate'],0.5)
                self.assertEqual(report['journeys'][1]['status'],'empty')
                (source/'J1-02/result.json').unlink()
                report=aggregate(source,destination)
                self.assertEqual(report['task']['completed'],1)
                self.assertEqual(report['task']['unknown'],1)
                self.assertEqual(report['confirmed_completion_rate'],0.5)
                self.assertEqual(report['test_errors'],1)
                self.assertEqual(report['cases'][1]['harness_errors'][0]['reason'],'case_artifact_missing')
                (source/'J1-02/result.json').write_text('{invalid')
                report=aggregate(source,destination)
                self.assertEqual(report['task']['completed'],1)
                self.assertEqual(report['cases'][1]['harness_errors'][0]['reason'],'case_result_unreadable')

    def test_case_cli_reports_actual_verdict_and_publishes_evidence_on_failure(self):
        for status, harness_error, expected_code in [('passed', False, 0), ('failed', False, 0),
                                                      ('timeout', False, 0), ('error', True, 1),
                                                      ('passed', True, 1)]:
            with self.subTest(status=status, harness_error=harness_error), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); case=root/'results/J1-01'; case.mkdir(parents=True)
                private='example-private-qa-token'
                turns=['plan','execute','review'] if status != 'timeout' else ['plan']
                tasks=[{'phase':'office:'+phase,'task_id':'task-'+str(i),'trace_id':'trace-'+str(i),
                        'conversation_id':'conversation-123','state':'completed' if status != 'timeout' else 'canceled',
                        'event_count':1} for i,phase in enumerate(turns)]
                result={'case_id':'J1-01', 'workspace':'journey-1', 'title':'办公用例', 'status':status,
                    'task_seconds':12.5, 'elapsed_seconds':14, 'capture_complete':True,
                    'tasks':tasks,
                    'sessions':[{'id':'office','outputs':{'contract-accepted.docx':'digest'}}] if status != 'timeout' else [],
                    'failures': [{'stage':'validation','reason':'acceptance_checks_failed','details':private}]
                        if status=='failed' else ([{'stage':'execute','reason':'case_timeout'}] if status=='timeout' else []),
                    'harness_errors':[{'stage':'semantic_judge','reason':'invalid_semantic_judge_verdict'}] if harness_error else [],
                    'validation':{'checks':[{'session':'office','rule':'数据完整','passed':status=='passed',
                                             'detail':'缺少数据' if status=='failed' else '核对输出'}],
                                  'failed_checks':[{'session':'office','rule':'数据完整','detail':'缺少数据'}] if status=='failed' else []}}
                (case/'result.json').write_text(json.dumps(result, ensure_ascii=False))
                (case/'events.jsonl').write_text(''.join(json.dumps({'task_id':task['task_id'],'seq':1,
                    'kind':'lifecycle','payload':{}})+'\n' for task in tasks))
                summary_path=root/'github-summary.md'
                env={**os.environ,'MOI_PAT':private,'MOI_BASE_URL':'','GITHUB_STEP_SUMMARY':str(summary_path)}
                process=subprocess.run([sys.executable,'-m','journey.report','case','J1-01','--root',str(root/'results')],
                    cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True)
                self.assertEqual(process.returncode,expected_code,process.stderr)
                markdown=(case/'case-summary.md').read_text()
                self.assertEqual(markdown,summary_path.read_text())
                self.assertIn(markdown,process.stdout)
                self.assertIn('trace-0',markdown)
                self.assertIn('conversation-123',markdown)
                self.assertIn('端到端任务耗时：12.5 秒',markdown)
                self.assertNotIn(private,markdown)
                if status=='failed':
                    self.assertIn('数据完整：缺少数据',markdown)
                    self.assertIn('[REDACTED]',markdown)
                if status=='timeout':self.assertIn('case_timeout',markdown)
                if harness_error:self.assertIn('测试框架：执行、验收或取证出错',markdown)
                published=root/'published'
                self.assertEqual(prepare_publication(root/'results',published,[private]),[])
                self.assertEqual((published/'J1-01/case-summary.md').read_text(),markdown)
                self.assertTrue((published/'J1-01/events.jsonl').exists())

    def test_case_cli_reports_setup_failure_when_task_never_started(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); case=root/'J1-01'; case.mkdir()
            (case/'preparation.json').write_text(json.dumps({'reason':'input_checksum_mismatch'}))
            env={**os.environ,'GITHUB_STEP_SUMMARY':''}
            process=subprocess.run([sys.executable,'-m','journey.report','case','J1-01','--root',str(root)],
                cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True)
            self.assertEqual(process.returncode,1,process.stderr)
            self.assertIn('测试框架：执行、验收或取证出错',process.stdout)
            self.assertIn('input_checksum_mismatch',process.stdout)
            self.assertIn('未获取会话或 trace ID',process.stdout)
            self.assertTrue((case/'case-summary.md').exists())

    def test_agent_failure_is_a_green_case_with_an_abnormal_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);case=root/'J1-01';case.mkdir()
            result={'status':'failed','capture_complete':True,'task_seconds':5,
                'tasks':[{'task_id':'task-1','conversation_id':'conversation-1','trace_id':'trace-1',
                          'phase':'office:plan','state':'failed','event_count':1}],
                'failures':[{'stage':'session:office','reason':'agent_failed',
                             'details':{'error':'inference unavailable'}}], 'harness_errors':[]}
            (case/'result.json').write_text(json.dumps(result))
            (case/'events.jsonl').write_text(json.dumps({'task_id':'task-1','seq':1,
                'kind':'lifecycle','payload':{'type':'session_info','session_id':'[redacted]'}})+'\n')
            env={**os.environ,'GITHUB_STEP_SUMMARY':''}
            process=subprocess.run([sys.executable,'-m','journey.report','case','J1-01','--root',str(root)],
                cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True)
            self.assertEqual(process.returncode,0,process.stderr)
            report=json.loads((case/'result.json').read_text())
            self.assertEqual((report['test_status'],report['task']['state'],report['agent']['state'],
                              report['delivery']['state'],report['quality']['state']),
                             ('completed','abnormal','abnormal','not_delivered','unavailable'))
            self.assertIn('inference unavailable',process.stdout)
            self.assertIn('conversation-1',process.stdout)
            self.assertIn('trace-1',process.stdout)
            self.assertIn('接口已脱敏',process.stdout)
            # A finalized report can be generated again without changing its result.
            self.assertEqual(finish_case('J1-01',root)['test_status'],'completed')

    def test_real_runner_failure_is_reported_without_failing_the_case_job(self):
        with qa_fixture('agent_failure') as (execution,_):
            raw=execution.run()
            self.assertEqual(raw['status'],'failed')
            case_root=execution.work/'finalized'
            shutil.copytree(execution.evidence.root,case_root/'J1-01')
            with patch('journey.report.load_case',return_value=execution.case), \
                 patch.object(sys,'argv',['journey.report','case','J1-01','--root',str(case_root)]), \
                 patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(report_main(),0)
            result=json.loads((case_root/'J1-01/result.json').read_text())
            self.assertEqual((result['test_status'],result['agent']['state'],result['task']['state']),
                             ('completed','abnormal','abnormal'))
            self.assertEqual(result['delivery']['state'],'not_delivered')
            self.assertTrue(result['capture_complete'])

    def test_finished_report_requires_actual_quality_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence=Evidence(root/'J1-01')
            evidence.write('result.json',{'status':'passed','capture_complete':True,'tasks':[],
                'failures':[],'harness_errors':[],'validation':{'checks':[]}})
            result=finish_case('J1-01',root)
            self.assertEqual(result['test_status'],'error')
            self.assertIn('quality_checks_missing',[e['reason'] for e in result['harness_errors']])
            self.assertEqual(result['quality']['state'],'unavailable')

    def test_aggregate_exit_follows_framework_status_and_keeps_all_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source'; destination=root/'bundle'
            config={'journeys':[{'workspace':'journey-1','concurrency':3,'cases':['J1-01','J1-02']}]}
            (root/'suites.json').write_text(json.dumps(config))
            for task_state, test_status, expected_code in [('completed','completed',0),('abnormal','completed',0),
                                                            ('timeout','completed',0),('unknown','error',1)]:
                with self.subTest(task_state=task_state):
                    for case_id, state, framework in [('J1-01','completed','completed'),('J1-02',task_state,test_status)]:
                        evidence=Evidence(source/case_id)
                        evidence.write('result.json',self.finished_result(case_id,task_state=state,test_status=framework))
                        (evidence.root/'events.jsonl').write_text('')
                    args=['journey.report','aggregate','--root',str(source),'--out',str(destination)]
                    with patch('journey.report.ROOT',root), patch.object(sys,'argv',args), \
                         patch.dict(os.environ,{'GITHUB_STEP_SUMMARY':''}), contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(report_main(),expected_code)
                    summary=json.loads((destination/'summary.json').read_text())
                    self.assertEqual(summary['total'],2)
                    self.assertEqual(summary['task']['completed'],2 if task_state=='completed' else 1)
                    self.assertEqual(summary['test_errors'],1 if test_status=='error' else 0)
                    self.assertEqual(summary['tools']['calls'],4)
                    self.assertEqual(summary['tools']['success_rate'],0.5)
                    self.assertEqual(summary['task_seconds']['median'],10)
                    self.assertTrue((destination/'cases/J1-01/events.jsonl').exists())
                    self.assertTrue((destination/'cases/J1-02/events.jsonl').exists())

    def test_aggregate_uses_call_weighted_rate_and_summarizes_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';destination=root/'bundle'
            (root/'suites.json').write_text(json.dumps({'journeys':[{'workspace':'journey-1','concurrency':3,
                                                                     'cases':['J1-01','J1-02']}]}))
            first=self.finished_result('J1-01')
            first['tools']['metrics'].update(calls=10,succeeded=9,failed=1,success_rate=0.9)
            first['tools']['metrics']['by_tool']=[{'tool':'bash','calls':10,'succeeded':9,'failed':1,'skipped':0,'unknown':0,'success_rate':0.9}]
            first['tools']['errors']=[{'tool':'bash','task_id':'t1','call_id':'c1','seqs':[1],
                                       'errors':[{'code':'upload_denied'}]}]
            second=self.finished_result('J1-02',task_state='timeout')
            second['tasks']=[{'conversation_id':None,'trace_id':None}]
            second['tools']['metrics'].update(calls=3,succeeded=0,failed=1,skipped=1,unknown=1,success_rate=0.0)
            second['tools']['metrics']['by_tool']=[{'tool':'bash','calls':3,'succeeded':0,'failed':1,'skipped':1,'unknown':1,'success_rate':0.0}]
            second['failures']=[{'stage':'session:office','reason':'case_timeout'}]
            for result in (first,second):Evidence(source/result['case_id']).write('result.json',result)
            with patch('journey.report.ROOT',root):
                report=aggregate(source,destination)
            self.assertEqual(report['tools']['success_rate'],9/11)
            self.assertEqual(report['tools']['by_tool'][0]['success_rate'],9/11)
            self.assertEqual((report['tools']['skipped'],report['tools']['unknown']),(1,1))
            self.assertEqual(report['task']['timeout'],1)
            self.assertEqual(report['tool_errors'][0]['code'],'upload_denied')
            self.assertEqual(report['tool_errors'][0]['cases'],['J1-01'])
            self.assertEqual(report['task_errors'][0]['reason'],'case_timeout')
            self.assertIn('upload_denied',(destination/'summary.md').read_text())

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
            self.assertEqual(report['test_status'],'error')
            self.assertEqual(report['task']['state'],'unknown')


if __name__=='__main__':unittest.main()
