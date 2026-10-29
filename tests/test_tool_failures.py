import copy
import json
import tempfile
import unittest
from pathlib import Path

from journey.tool_failure_checks import evaluate_tool_failure
from journey.search_checks import evaluate_search


class ToolFailureTests(unittest.TestCase):
    def run_check(self, events, config):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl'
            path.write_text(''.join(json.dumps(e)+'\n' for e in events))
            return evaluate_tool_failure(path,'task','session',config)

    def events(self,args,output,success=False,tool='mcp__moi-tools__bash'):
        return [{'task_id':'task','kind':'tool_call','payload':{'tool_id':tool,'call_id':'c','arguments':args}},
                {'task_id':'task','kind':'tool_result','payload':{'tool_id':tool,'call_id':'c','success':success,
                 'error':'TOOL_EXECUTION_FAILED' if not success else None,'output':output}}]

    def test_timeout_requires_bound_timeout_actual_error_and_matching_task(self):
        events=self.events({'command':"python3 -c 'print(\"MARK\"); import time; time.sleep(3)'",'timeout':0.1},'command timed out')
        config={'kind':'timeout','marker':'MARK'}
        self.assertTrue(all(c['passed'] for c in self.run_check(events,config)))
        for fault in ['task','call','no_timeout','wrong_error','success']:
            changed=copy.deepcopy(events)
            if fault=='task':changed[-1]['task_id']='other'
            elif fault=='call':changed[-1]['payload']['call_id']='other'
            elif fault=='no_timeout':changed[0]['payload']['arguments'].pop('timeout')
            elif fault=='wrong_error':changed[-1]['payload']['output']='connection refused'
            else:changed[-1]['payload']['success']=True
            self.assertFalse(all(c['passed'] for c in self.run_check(changed,config)),fault)

    def test_schema_rejection_does_not_accept_network_validation_or_success(self):
        config={'kind':'arguments','tool_id':'owned'}
        for message,success,expected in [('invalid argument unexpected',False,True),('TLS validation failure',False,False),
                                         ('invalid argument unexpected',True,False),('HTTP 404',False,False)]:
            checks=self.run_check(self.events({'unexpected':1},message,success,'owned'),config)
            self.assertEqual(checks[-1]['passed'],expected)

    def test_size_error_keeps_verified_command_exit_reference(self):
        events=self.events({'command':'moi-cli -o file upload --file /tmp/fixture.bin --publish-artifact'},
            {'output':{'exit_code':1,'stderr':'{"code":"runtime_artifact_upload_denied","status":413}'}},True)
        checks=self.run_check(events,{'kind':'artifact_size','marker':'fixture.bin'})
        self.assertTrue(checks[-1]['passed'])
        self.assertEqual(checks[-1]['expected_tool_errors'],[{'task_id':'task','call_id':'c','kind':'command_exit','code':'1'}])
        events[-1]['payload']['output']['output']['exit_code']=0
        self.assertFalse(self.run_check(events,{'kind':'artifact_size','marker':'fixture.bin'})[-1]['passed'])

    def test_missing_search_channel_is_blocked_only_for_exact_matched_provider_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'events.jsonl'
            for provider in ['bocha','tavily','brave']:
                tool='moi_'+provider+'_web_search'
                message='automation runtime tool credential is invalid: no usable '+provider+' Channel instance is configured for this workspace'
                events=self.events({'query':'fixture'},message,False,tool)
                path.write_text(''.join(json.dumps(e)+'\n' for e in events))
                self.assertEqual(evaluate_search(path,'task','','session',expected_tool=tool)[1],'channel_not_configured:'+provider)
                events[-1]['payload']['call_id']='foreign'
                path.write_text(''.join(json.dumps(e)+'\n' for e in events))
                self.assertNotEqual(evaluate_search(path,'task','','session',expected_tool=tool)[1],'channel_not_configured:'+provider)
