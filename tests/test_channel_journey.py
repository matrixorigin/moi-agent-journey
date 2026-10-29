import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.builder_flow import ProductCaseFailure
from journey.channel_journey import ChannelUnavailable, channel_targets, evaluate_channel_call, resolve_values
from journey.channel_receivers import verify_receiver
from journey.evidence import Evidence, configured_secrets


class ChannelTests(unittest.TestCase):
    def execution(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        return SimpleNamespace(root='/workspaces/ws',report={},checkpoint=Mock(),
            client=Mock(),evidence=Evidence(Path(directory.name)))

    def test_all_missing_mail_prerequisites_are_reported_before_any_writes(self):
        ex=self.execution();ex.client.product.return_value={'items':[]};checks=[]
        with patch.dict(os.environ,{'MOI_J2_CHANNEL_TARGETS':'{}'}):
            with self.assertRaises(ChannelUnavailable):channel_targets(ex,['qq_mail','wecom_mail'],checks)
        self.assertEqual(len(checks),2)
        self.assertTrue(all(c.args[0]=='GET' for c in ex.client.product.call_args_list))
        self.assertEqual([item['provider'] for item in ex.report['channel_preflight']],['qq_mail','wecom_mail'])

    def test_matching_instance_requires_workspace_type_active_status_and_explicit_target(self):
        target={'github':{'instance_id':'owned','owner':'test-org','repo':'test-repo'}}
        item={'id':'owned','workspace_id':'ws','provider':'github','channel_type':'github_app','status':'active'}
        for field in [None,'workspace_id','channel_type','status','id']:
            ex=self.execution();current=dict(item)
            if field:current[field]='wrong'
            ex.client.product.return_value={'items':[current]}
            with patch.dict(os.environ,{'MOI_J2_CHANNEL_TARGETS':json.dumps(target)}):
                if field:
                    with self.assertRaises(ChannelUnavailable):channel_targets(ex,['github'],[])
                else:self.assertEqual(channel_targets(ex,['github'],[]),target)

    def test_slack_requires_receiver_credential_before_sending(self):
        ex=self.execution();ex.client.product.return_value={'items':[{'id':'owned','workspace_id':'ws','provider':'slack','channel_type':'slack_app','status':'active'}]}
        with patch.dict(os.environ,{'MOI_J2_CHANNEL_TARGETS':json.dumps({'slack':{'instance_id':'owned','channel_id':'C1'}})},clear=True):
            with self.assertRaisesRegex(ChannelUnavailable,'receiver_read_credential_missing'):channel_targets(ex,['slack'],[])

    def test_exact_target_result_and_call_identity_before_capturing_receipt(self):
        spec={'tool':'moi_slack_text_message','arguments':{'channel_id':'C1','text':'{code}'},
              'result_fields':{'ok':True,'channel_id':'C1'},'capture':{'ts':'timestamp'}}
        original=[{'task_id':'t','kind':'tool_call','payload':{'tool_id':spec['tool'],'call_id':'c','arguments':{'channel_id':'C1','text':'NONCE'}}},
                  {'task_id':'t','kind':'tool_result','payload':{'tool_id':spec['tool'],'call_id':'c','success':True,'output':{'output':{'ok':True,'channel_id':'C1','timestamp':'123.45'}}}}]
        for fault in [None,'wrong_task','wrong_recipient','extra_recipient','duplicate','missing_receipt','failed']:
            ex=self.execution();events=copy.deepcopy(original);variables={'code':'NONCE'}
            if fault=='wrong_task':events[-1]['task_id']='other'
            elif fault=='wrong_recipient':events[0]['payload']['arguments']['channel_id']='C2'
            elif fault=='extra_recipient':events[0]['payload']['arguments']['to']='other'
            elif fault=='duplicate':
                extra=copy.deepcopy(events[0]);extra['payload']['call_id']='again';events.append(extra)
            elif fault=='missing_receipt':events[-1]['payload']['output']['output'].pop('timestamp')
            elif fault=='failed':events[-1]['payload']['success']=False
            ex.evidence.write('unused.json',{})
            (ex.evidence.root/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
            if fault:
                with self.assertRaises(ProductCaseFailure):evaluate_channel_call(ex,'t','slack',spec,variables,[])
                self.assertNotIn('ts',variables)
            else:
                evaluate_channel_call(ex,'t','slack',spec,variables,[]);self.assertEqual(variables['ts'],'123.45')

    def test_slack_readback_requires_same_message_and_content(self):
        for timestamp,body,passed in [('123.45','NONCE',True),('999','NONCE',False),('123.45','old',False)]:
            ex=self.execution();checks=[]
            with patch.dict(os.environ,{'MOI_J2_SLACK_READ_TOKEN':'private-read-value'}), patch('journey.channel_receivers.provider_json',return_value={
                'ok':True,'messages':[{'ts':timestamp,'text':body}]}) as request:
                if passed:verify_receiver(ex,'slack',{'channel_id':'C1','timestamp':'123.45'},'NONCE','text','s',checks)
                else:
                    with self.assertRaises(ProductCaseFailure):verify_receiver(ex,'slack',{'channel_id':'C1','timestamp':'123.45'},'NONCE','text','s',checks)
                self.assertIn('channel=C1',request.call_args.args[1])
                self.assertEqual(checks[-1]['passed'],passed)

    def test_dynamic_ids_keep_type_and_receiver_secrets_are_redacted(self):
        self.assertEqual(resolve_values({'number':'{issue}'},{'issue':42}),{'number':42})
        with patch.dict(os.environ,{'MOI_J2_SLACK_READ_TOKEN':'private-token-fixture','MOI_J2_FEISHU_APP_SECRET':'private-app-fixture'}):
            ex=self.execution();e=Evidence(ex.evidence.root,configured_secrets())
            self.assertEqual(e.text('private-token-fixture private-app-fixture'),'[REDACTED] [REDACTED]')
