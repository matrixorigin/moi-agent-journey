import copy
import hashlib
import io
import json
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.client import Failure
from journey.evidence import Evidence
from journey.native_skill_checks import business_effect, canonical, confirmation_reply, csv_matches, observations, same_records, query_result_matches
from journey.native_skill_data import NativeSkillData
from journey.native_skill_cleanup import delete_owned_file, cleanup_artifacts, same_cleanup_command
from journey.native_skill_source import read_package, with_unique_name
from journey.native_skill_journey import NativeSkillJourney
from journey.owned_skill_journey import OwnedSkillJourney


def snapshot():
    return {'tables': ['customers', 'progress_items', 'progress_changes'], 'employees': [],
            'customers': [], 'progress_items': [], 'progress_changes': []}


def proposed(name='customer', change_id='abcdef123456'):
    record = {'customer_uuid': 'customer-id-' + change_id, 'name': name, 'version': 1}
    return {'change_id': change_id, 'entity': 'customer', 'action': 'create', 'record_id': record['customer_uuid'],
            'status': 'pending', 'branch_name': 'poc_cus_' + change_id, 'before_json': 'null',
            'after_json': json.dumps(record), 'created_by': 'tester', 'updated_at': '2026-01-01T00:00:00'}


class NativeSkillTests(unittest.TestCase):
    def test_sql_datetime_transport_preserves_microseconds_and_date_types(self):
        self.assertEqual(canonical({'updated_at':'2026-01-01 00:00:00.123456 +0000 UTC','expected_end':'2100-01-01 00:00:00 +0000 UTC'}),
                         canonical({'updated_at':'2026-01-01 00:00:00.123456','expected_end':'2100-01-01'}))
        self.assertEqual(canonical({'updated_at':'2026-01-01 00:00:00.12345 +0000 UTC'}), canonical({'updated_at':'2026-01-01 00:00:00.123450'}))
        self.assertNotEqual(canonical({'updated_at':'2026-01-01 00:00:00.123456'}),canonical({'updated_at':'2026-01-01 00:00:00.123457'}))

    def test_source_pin_and_name_change_do_not_replace_scripts(self):
        files = {'SKILL.md': b'---\nname: '+ '进度管理'.encode() + b'\n---\nbody\n', 'scripts/progress.py': b'print(1)'}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            for name, value in files.items(): z.writestr(name, value)
        raw=stream.getvalue(); definition={'files': list(files), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.assertEqual(read_package(raw, definition), files)
        with self.assertRaises(Failure): read_package(raw+b'x', definition)
        with self.assertRaises(Failure): read_package(raw, {**definition, 'files':['SKILL.md']})
        renamed, body=with_unique_name(files, '进度管理-j2-'+'a'*32)
        self.assertEqual(renamed['scripts/progress.py'], files['scripts/progress.py'])
        self.assertEqual(body, 'body')
        with self.assertRaises(Failure): with_unique_name(files, '../other')

    def test_preview_requires_unchanged_main_existing_intents_and_exact_branches(self):
        before=snapshot(); item=proposed(); after=copy.deepcopy(before)
        after['progress_changes']=[item]; after['tables'].append(item['branch_name'])
        spec={'kind':'preview','expected':[{'entity':'customer','action':'create','capture':'id','fields':{'name':'customer'}}]}
        variables={}; self.assertTrue(business_effect(spec,before,after,variables)[0]); self.assertEqual(variables['id'],item['change_id'])
        for broken in ('main','branch','wrong_field','malformed_json','duplicate'):
            bad=copy.deepcopy(after)
            if broken=='main':bad['customers']=[json.loads(item['after_json'])]
            if broken=='branch':bad['tables'].append('unrelated')
            if broken=='wrong_field':bad['progress_changes'][0]['after_json']='{"name":"wrong"}'
            if broken=='malformed_json':bad['progress_changes'][0]['before_json']='bad'
            if broken=='duplicate':bad['progress_changes'].append(item)
            variables={};self.assertFalse(business_effect(spec,before,bad,variables)[0],broken);self.assertEqual(variables,{})
        old=proposed('older','123456abcdef'); before['progress_changes']=[old];before['tables'].append(old['branch_name'])
        after['progress_changes'].append({**old,'status':'committed'});after['tables'].append(old['branch_name'])
        self.assertFalse(business_effect(spec,before,after,{})[0])

    def test_confirmation_cannot_commit_unselected_preview_or_leave_branch(self):
        before=snapshot();a=proposed();b=proposed('second','123456abcdef')
        before['progress_changes']=[a,b];before['tables'] += [a['branch_name'],b['branch_name']]
        after=copy.deepcopy(before);after['progress_changes'][0]['status']='committed';after['tables'].remove(a['branch_name']);after['customers']=[json.loads(a['after_json'])]
        spec={'kind':'settle','ids':[a['change_id']],'status':'committed'}
        self.assertTrue(business_effect(spec,before,after,{})[0])
        for broken in ('unselected','branch','wrong_record','mutated_ledger'):
            bad=copy.deepcopy(after)
            if broken=='unselected':bad['progress_changes'][1]['status']='committed';bad['customers'].append(json.loads(b['after_json']))
            if broken=='branch':bad['tables'].append(a['branch_name'])
            if broken=='wrong_record':bad['customers'][0]['name']='incorrect'
            if broken=='mutated_ledger':bad['progress_changes'][0]['created_by']='other'
            self.assertFalse(business_effect(spec,before,bad,{})[0],broken)

    def test_cancellation_leaves_main_untouched(self):
        before=snapshot();a=proposed();before['progress_changes']=[a];before['tables'].append(a['branch_name'])
        after=copy.deepcopy(before);after['tables'].remove(a['branch_name']);after['progress_changes'][0]['status']='canceled'
        spec={'kind':'settle','ids':[a['change_id']],'status':'canceled'}
        self.assertTrue(business_effect(spec,before,after,{})[0])
        after['customers']=[json.loads(a['after_json'])];self.assertFalse(business_effect(spec,before,after,{})[0])

    def test_replies_and_csv_preserve_real_ids_and_leading_zeros(self):
        self.assertFalse(confirmation_reply('已完成', ['abcdef123456']))
        self.assertTrue(confirmation_reply('## 变更确认\n- [ ] 确认 abcdef123456', ['abcdef123456'], checklist=True))
        rows=[{'owner_eid':'0007','owner_name':'林青','item_count':'2'}]
        self.assertTrue(csv_matches('owner_eid,owner_name,item_count\n0007,林青,2\n'.encode(),rows))
        self.assertFalse(csv_matches('owner_eid,owner_name,item_count\n7,林青,2\n'.encode(),rows))
        self.assertFalse(same_records([{'id':'x'}],[{'id':'x'},{'id':'x'}],'id'))

    def test_observations_require_same_task_and_call_successful_script_stdout(self):
        def event(kind,tool,call,payload,task='task'):
            return {'kind':kind,'task_id':task,'payload':{'tool_id':tool,'call_id':call,**payload}}
        events=[event('tool_call','skill','s',{'arguments':{'skill_name':'own'}}),
                event('tool_result','skill','s',{'success':True}),
                event('tool_call','mcp__moi-tools__bash','b',{'arguments':{'command':'download-skill-files --skill-id own && python3 progress.py query_progress'}}),
                event('tool_result','mcp__moi-tools__bash','b',{'success':True,'output':{'output':{'exit_code':0,'stdout':'{"rows":[],"truncated":false}'}}})]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'events.jsonl'
            def write():p.write_text('\n'.join(json.dumps(e) for e in events))
            write();d,l,s=observations(p,'task','own','name');self.assertTrue(d and l and s[0]['succeeded'])
            events[-1]['payload']['payload_truncated']=True;write();self.assertTrue(observations(p,'task','own','name')[2][0]['truncated']);del events[-1]['payload']['payload_truncated']
            events[-1]['task_id']='other';write();d,l,s=observations(p,'task','own','name');self.assertTrue(d);self.assertFalse(l or s[0]['succeeded'])
            events[-1]['task_id']='task';events[-1]['payload']['output']['output']['exit_code']=1;write();self.assertFalse(observations(p,'task','own','name')[2][0]['succeeded'])

    def test_cleanup_uses_only_owned_identity_and_stops_for_running_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            execution=SimpleNamespace(report={'run_id':str(uuid.uuid4()),'harness_errors':[]},client=Mock(deadline=time.monotonic()+30),
                case={'id':'J2-111'},evidence=Evidence(Path(tmp)),checkpoint=lambda:None)
            data=NativeSkillData(execution)
            for database in data.databases.values():database.update(id=1,catalog_id=1)
            data.cleanup(False);execution.client.product.assert_not_called();execution.client.request.assert_not_called()
            self.assertEqual(len(execution.report['harness_errors']),2)
            execution.client.product.side_effect=lambda method,path,body,**kw: {'database':{'id':1,'name':'foreign'}} if path.endswith('/info') else {'list':[]}
            data.cleanup(True);execution.client.request.assert_not_called()

    def test_query_values_accept_row_projection_but_not_claims_or_duplicate_rows(self):
        expected=[{'item_id':'i','owner_eid':'0007','owner_name':'N'}]
        script={'succeeded':True,'command':'python3 progress.py query_progress','objects':expected}
        self.assertTrue(query_result_matches([script],expected))
        self.assertTrue(query_result_matches([{**script,'objects':[{'rows':expected,'truncated':False}]}],expected))
        self.assertFalse(query_result_matches([{**script,'objects':[{'message':'success'}]}],expected))
        self.assertFalse(query_result_matches([{**script,'objects':expected*2}],expected))
        self.assertFalse(query_result_matches([{**script,'objects':[{'rows':expected,'truncated':True}]}],expected))
        self.assertFalse(query_result_matches([{**script,'succeeded':False}],expected))

    def test_cleanup_command_accepts_only_same_python_program(self):
        import shlex
        command = 'python3 -c ' + shlex.quote("print({'path':'/tmp/owned','exists':False})")
        same = 'python3 -c ' + shlex.quote('print({"path":"/tmp/owned","exists":False})')
        self.assertTrue(same_cleanup_command(same,command))
        self.assertFalse(same_cleanup_command(command+'; echo done',command))
        self.assertFalse(same_cleanup_command(same.replace('owned','foreign'),command))

    def test_file_cleanup_understands_omitted_zero_refcount_and_verifies_ownership(self):
        execution=SimpleNamespace(root='/workspaces/ws',client=Mock(deadline=time.monotonic()+30),evidence=Mock())
        own={'file_id':'file','source_session_id':'cid','source_task_id':'task'}
        execution.client.product.return_value=own
        execution.client.request.side_effect=[b'',Failure('http_error',404)]
        delete_owned_file(execution,'file','cid',{'task'})
        self.assertEqual(execution.client.request.call_args_list[0].args[0],'DELETE')
        for bad in ({**own,'ref_count':1},{**own,'source_session_id':'other'},{**own,'source_task_id':'other'}):
            execution.client.deadline=time.monotonic()
            execution.client.request.reset_mock();execution.client.product.return_value=bad
            with self.assertRaises(Failure):delete_owned_file(execution,'file','cid',{'task'})
            execution.client.request.assert_not_called()

    def test_all_artifact_references_are_removed_before_shared_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence=Evidence(Path(tmp));artifacts=[{'artifact_id':'artifact-'+str(i),'version':1,'file_id':'file',
                'conversation_id':'cid','task_id':'task','agent_id':'agent','agent_workspace_id':'ws'} for i in (1,2)]
            evidence.write('artifacts/cid/inventory.json',artifacts)
            client=Mock();client.product.return_value={'succeeded':[{'artifact_id':a['artifact_id'],'version':1} for a in artifacts],'failed':[]}
            execution=SimpleNamespace(root='/workspaces/ws',client=client,evidence=evidence,tasks=[{'task_id':'task','conversation_id':'cid'}],
                conversation_selector=lambda _: {'agent_id':'agent','agent_workspace_id':'ws'},checkpoint=lambda:None)
            journey=SimpleNamespace(execution=execution,state={})
            with patch('journey.native_skill_cleanup.delete_owned_file') as delete:
                cleanup_artifacts(journey,'cid');delete.assert_called_once_with(execution,'file','cid',{'task'})
            self.assertEqual(len(client.product.call_args.args[2]['items']),2)

    def test_collection_error_cannot_mark_output_cleanup_completed(self):
        journey=NativeSkillJourney.__new__(NativeSkillJourney)
        journey.state={}
        journey.data=SimpleNamespace(databases={'sales':{'cleanup':'deleted'}},cleanup=Mock())
        journey.execution=SimpleNamespace(conversations=[{'cleanup':'incomplete'}],checkpoint=lambda:None)
        with patch.object(OwnedSkillJourney,'cleanup',side_effect=lambda **kwargs:journey.state.update(cleanup='completed')):
            journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'],'incomplete')

    def test_independent_file_cleanup_runs_even_when_sandbox_cleanup_fails(self):
        journey=SimpleNamespace(state={},execution=SimpleNamespace(checkpoint=lambda:None))
        with patch('journey.native_skill_resources.cleanup_sandbox',side_effect=Failure('error')), patch('journey.native_skill_resources.cleanup_artifacts') as artifacts:
            with self.assertRaises(Failure):NativeSkillJourney.cleanup_outputs(journey,'cid')
            artifacts.assert_called_once_with(journey,'cid')


if __name__=='__main__':unittest.main()
