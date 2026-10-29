import copy
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from journey.a2ui_checks import CATALOG, form_action
from journey.agent_package_journey import AgentPackageJourney, package_bytes, package_files
from journey.citation_checks import check_citation_open
from journey.client import Failure
from journey.evidence import Evidence
from journey.knowledge_fixture import KnowledgeFixture
from journey.catalog import load_case
from journey.pagination_checks import check_pagination
from journey.publish import prepare_publication


class ExtendedTests(unittest.TestCase):
    def execution(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        return SimpleNamespace(root='/workspaces/ws',case={'sessions':[{'id':'owned'}]},
            report={'run_id':'run-123','harness_errors':[]},client=Mock(deadline=time.monotonic()+30,observer=None),
            checkpoint=Mock(),evidence=Evidence(Path(directory.name)),conversation_selector=lambda _: {'agent_id':'agent','agent_workspace_id':'ws'})

    def test_export_archive_rejects_content_tampering_and_path_traversal(self):
        files={'agent.yaml':b'id: owned\n','prompts/main.md':b'owned instructions'}
        valid=package_bytes(files)
        self.assertEqual(package_files(valid)['prompts/main.md'],files['prompts/main.md'])
        for fault in ['checksum','path','duplicate']:
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as out:
                with zipfile.ZipFile(io.BytesIO(valid)) as original:
                    for name in original.namelist():
                        value=original.read(name)
                        if fault=='checksum' and name=='prompts/main.md':value=b'changed'
                        out.writestr(name,value)
                if fault=='path':out.writestr('../escape',b'bad')
                if fault=='duplicate':
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore');out.writestr('agent.yaml',b'bad')
            with self.assertRaises(Failure):package_files(stream.getvalue())

    def test_package_cleanup_never_archives_a_foreign_identity(self):
        ex=self.execution();journey=AgentPackageJourney(ex,{'mode':'invalid'},[]);journey.agent_pending=True
        ex.client.product.return_value={'id':journey.agent_id,'name':'another-agent','workspace_id':'ws','status':'active'}
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'],'incomplete')
        self.assertFalse(any(c.args[0]=='PATCH' for c in ex.client.product.call_args_list))

    def test_exported_package_publishes_as_binary_and_still_scans_nested_secrets(self):
        ex = self.execution()
        ex.model = 'fixture-model'
        ex.evidence = Evidence(ex.evidence.root / 'results' / 'J2-98')
        journey = AgentPackageJourney(ex, {'mode': 'roundtrip'}, [])
        agent_id = journey.agent_id
        files = {'agent.yaml': ('schemaVersion: 1\nid: ' + agent_id + '\nversion: 1.0.0\nname: ' + agent_id +
            '\nmodel: fixture-model\ninstruction:\n  path: prompts/main.md\nmoiResourceRefs:\n'
            '  tools: []\n  skills: []\n  knowledgeBases: []\n  catalogFiles: []\n  channelBindings: []\n').encode(),
            'prompts/main.md': b'Always respond only with this code: OLD-run-123'}
        exported = package_bytes(files)
        journey.absent = Mock(return_value=True)
        journey.load = Mock(side_effect=[{'version': {'agent_id': agent_id, 'version': version, 'status': 'runnable'}}
                                        for version in ('1.0.0', '1.0.1')])
        journey.versions = Mock(side_effect=[{'items': [{'version': '1.0.0'}]},
            {'items': [{'version': '1.0.0'}, {'version': '1.0.1'}], 'lineage': {'default_version': '1.0.1'}}])
        journey.request = Mock(side_effect=[{'id': agent_id, 'name': agent_id, 'workspace_id': 'ws', 'status': 'active'},
                                            {'agent_id': agent_id, 'default_version': '1.0.1'}])
        ex.client.request.return_value = exported
        journey.prepare()
        self.assertTrue(all(item['passed'] for item in journey.checks))
        public = ex.evidence.root.parent.parent / 'public'
        self.assertEqual(prepare_publication(ex.evidence.root.parent, public, ['private-package-fixture']), [])
        target = public / 'J2-98' / 'outputs' / 'package' / 'export.zip'
        self.assertEqual(target.read_bytes(), exported)
        (ex.evidence.root / 'outputs' / 'package' / 'export.zip').write_bytes(package_bytes(
            {**files, 'prompts/main.md': b'private-package-fixture'}))
        withheld = prepare_publication(ex.evidence.root.parent, public.with_name('withheld'), ['private-package-fixture'])
        self.assertEqual(len(withheld), 1)
        self.assertEqual(withheld[0]['reason'], 'credential_detected_in_artifact')
        self.assertFalse((public.with_name('withheld') / 'J2-98' / 'outputs' / 'package' / 'export.zip').exists())

    def test_form_uses_observed_paths_and_rejects_unbound_or_foreign_actions(self):
        components=[{'id':'root','component':'Column','children':['form']},
            {'id':'form','component':'Form','children':['name','choice','submit']},
            {'id':'name','component':'TextField','label':'Name','value':{'path':'/input/n'}},
            {'id':'choice','component':'ChoicePicker','label':'Type','value':{'path':'/input/t'},'options':[{'label':'A','value':'a'}]},
            {'id':'submit','component':'Button','action':{'event':{'name':'observed_submit','context':{'n':{'path':'/input/n'},'t':{'path':'/input/t'}}}}}]
        events=[{'task_id':'task','kind':'a2ui','payload':{'version':'v0.9','createSurface':{'surfaceId':'surface','catalogId':CATALOG}}},
                {'task_id':'task','kind':'a2ui','payload':{'version':'v0.9','updateComponents':{'surfaceId':'surface','components':components}}}]
        for fault in [None,'foreign_task','unbound','duplicate_path','invalid_choice']:
            ex=self.execution();changed=copy.deepcopy(events);fields={'Name':'value','Type':['a']}
            nodes=changed[-1]['payload']['updateComponents']['components']
            if fault=='foreign_task':changed[-1]['task_id']='other'
            elif fault=='unbound':nodes[-1]['action']['event']['context']['n']['path']='/other'
            elif fault=='duplicate_path':nodes[3]['value']['path']='/input/n'
            elif fault=='invalid_choice':fields['Type']=['missing']
            path=ex.evidence.root/'events.jsonl';path.write_text(''.join(json.dumps(e)+'\n' for e in changed))
            action=form_action(path,'task',fields)
            if fault:self.assertIsNone(action,fault)
            else:
                self.assertEqual(action['action']['sourceComponentId'],'submit')
                self.assertEqual(action['action']['context'],{'n':'value','t':['a']})

    def test_pdf_fixture_verifies_marker_in_extracted_pdf_not_raw_utf8(self):
        ex=self.execution();ex.case=load_case('J2-100')
        fixture=KnowledgeFixture(ex,ex.case['knowledge_fixtures']['pdf'],alias='pdf')
        self.assertEqual(fixture.marker,'VT-5634')
        invalid={**ex.case['knowledge_fixtures']['pdf'],'marker':'NOT-IN-PDF'}
        with self.assertRaisesRegex(Failure,'knowledge_fixture_marker_missing'):KnowledgeFixture(ex,invalid,alias='pdf')

    def test_citation_preview_never_opens_reference_from_another_task(self):
        ex=self.execution();fixture=SimpleNamespace(kind='document',state={'model_id':1,'file_id':'ours','source_id':'source'},product=Mock())
        event={'task_id':'other','kind':'artifact','payload':{'artifact':{'metadata':{'matrixflow_type':'knowledge.answer',
            'source_refs':[{'type':'rag_chunk','semantic_model_id':1,'file_id':'ours','source_row_id':'source'}]}}}}
        (ex.evidence.root/'events.jsonl').write_text(json.dumps(event)+'\n');checks=[]
        check_citation_open(ex,'conversation','task','owned',checks,fixture)
        self.assertTrue(all(not c['passed'] for c in checks))
        ex.client.request.assert_not_called();fixture.product.assert_not_called()

    def test_task_total_must_stay_consistent_on_the_empty_terminal_page(self):
        ex=self.execution();scope=ex.conversation_selector('c');ex.case={'id':'J2-102'}
        ex.conversations=[{'id':'c'+str(i),'selector':scope} for i in range(3)]
        ex.tasks=[{'task_id':'t1','conversation_id':'c2'},{'task_id':'t2','conversation_id':'c2'}]
        from urllib.parse import urlsplit,parse_qs
        def product(method,path,body=None):
            uri=urlsplit(path);query=parse_qs(uri.query)
            if method=='PATCH':return {}
            if uri.path.rsplit('/',1)[-1] in {'c0','c1','c2'}:
                return {'id':uri.path.rsplit('/',1)[-1],'workspace_id':'ws',**scope}
            offset=int(query['offset'][0]);total=2
            if uri.path.endswith('/conversations'):
                rows=[{'id':'c2'}] if query.get('pinned') else [{'id':'c'+str(i)} for i in range(3)];total=len(rows)
            elif uri.path.endswith('/agent-runtime-tasks'):
                rows=[{'id':'t1','context_id':'c2'},{'id':'t2','context_id':'c2'}];total=0 if offset==2 else 2
            else:rows=[{'id':'m'+str(i),'task_id':'t'+str(i+1),'conversation_id':'c2','role':query['role'][0]} for i in range(2)]
            return {'items':rows[offset:offset+1],'total':total}
        ex.client.product.side_effect=product;checks=[]
        check_pagination(ex,'c2','owned',checks)
        self.assertEqual([c['detail'] for c in checks if not c['passed']],['task_pages_mismatch'])
