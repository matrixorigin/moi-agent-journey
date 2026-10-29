import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence
from journey.functional_case import FunctionalExecution
from journey.native_skill_builder import NativeSkillBuilderJourney
from test_journeys import qa_fixture


class NativeBuilderTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        execution = SimpleNamespace(root='/workspaces/ws', case={'id':'J2-112','sessions':[{'id':'builder'}]},
            report={'run_id':'12345678-1234-1234-1234-123456789abc','harness_errors':[]},
            client=Mock(deadline=time.monotonic()+60), evidence=Evidence(Path(directory.name)), checkpoint=lambda:None)
        journey = NativeSkillBuilderJourney(execution, [])
        journey.employees_table = 'default.owned.employees'
        return journey, execution

    def test_builder_prepares_only_employee_database_and_does_not_manually_publish(self):
        journey, execution = self.fixture()
        self.assertEqual(set(journey.data.databases), {'employees'})
        journey.prepare_resources = Mock(return_value={'employees_table':journey.employees_table})
        journey.request = Mock(return_value={'total':0,'items':[]})
        journey.publish_agent = Mock()
        variables = journey.prepare()
        self.assertEqual(variables['native_agent_name'], journey.agent_name)
        self.assertNotIn('sales_database', variables)
        journey.publish_agent.assert_not_called()
        journey.data.cleanup(True)
        execution.client.request.assert_not_called()
        self.assertEqual(journey.data.databases['employees']['cleanup'], 'not_created')

    def test_preflight_requires_real_success_correct_source_and_readonly_data(self):
        for defect in ('none','column_order','path','readonly','columns','exit','discovery','download','mutated','extra_table'):
            with self.subTest(defect=defect):
                journey, _ = self.fixture()
                obj={'status':'validated','read_only':True,'employees_table':journey.employees_table,
                     'missing_optional_columns':['english_name','hiring_date','is_active']}
                script={'succeeded':True,'command':'python3 progress.py check_employees','objects':[obj]}
                rows=[{'employee_id':'0007','name':'Lin'}]
                journey.data.sql=Mock(side_effect=[copy.deepcopy(rows),[{'table':'employees'}]])
                journey.data.state['employees_before']=copy.deepcopy(rows)
                if defect=='path':obj['employees_table']='default.foreign.employees'
                if defect=='readonly':obj['read_only']=False
                if defect=='columns':obj['missing_optional_columns']=[]
                if defect=='column_order':obj['missing_optional_columns'].reverse()
                if defect=='exit':script['succeeded']=False
                if defect=='mutated':journey.data.state['employees_before'][0]['name']='Changed'
                if defect=='extra_table':journey.data.sql.side_effect=[rows,[{'table':'employees'},{'table':'customers'}]]
                with patch('journey.native_skill_builder.observations',return_value=(defect!='discovery',defect!='download',[script])):
                    journey.check_execution('task','published:check')
                self.assertEqual(all(c['passed'] for c in journey.checks),defect in {'none','column_order'})
                self.assertTrue(all(c['session']=='published' for c in journey.checks))

    def test_confirm_uses_generated_identity_and_owns_binding_before_uncertain_response(self):
        journey, _ = self.fixture()
        name=journey.agent_name
        journey.builder=Mock(current={'agent_id':'generated-id'},created={'version':'1.0.0'})
        journey.builder.commit.side_effect=Failure('network_error')
        with self.assertRaises(Failure):journey.commit({})
        self.assertEqual(journey.agent_id,'generated-id');self.assertEqual(journey.agent_name,name)
        self.assertTrue(journey.agent_pending)
        journey.builder.require_resource.assert_called_once_with('skills',journey.skill_id,workspace_id='ws')
        journey, _ = self.fixture();journey.builder=Mock()
        journey.builder.remember.side_effect=ProductCaseFailure('candidate_missing')
        with self.assertRaises(ProductCaseFailure):journey.commit({})
        self.assertFalse(journey.agent_pending);journey.builder.commit.assert_not_called()

    def test_cleanup_accepts_generated_id_only_with_exact_requested_name(self):
        for name in ('owned-name','foreign-name'):
            journey, execution=self.fixture();journey.agent_id='generated-id';journey.agent_name='owned-name'
            journey.agent_pending=True;journey.builder=None
            agent={'id':journey.agent_id,'workspace_id':'ws','name':name,'status':'archived'}
            execution.client.product.side_effect=[agent,agent,{},{}]
            if name=='owned-name':
                journey.detach_agent_resources({journey.skill_id},agent_name=journey.agent_name)
            else:
                with self.assertRaises(Failure):journey.detach_agent_resources({journey.skill_id},agent_name=journey.agent_name)
            mutations=[c for c in execution.client.product.call_args_list if c.args[0]=='PATCH']
            self.assertEqual(len(mutations),int(name=='owned-name'))

    def test_published_agent_must_persist_both_actual_source_and_owned_directory(self):
        for configuration in ('complete','missing_directory','missing_source','invalid'):
            with self.subTest(configuration=configuration):
                journey, _ = self.fixture()
                journey.builder=Mock(current={'agent_id':'generated-id'},created={'version':'1.0.0'})
                instruction={'complete':journey.employees_table+' '+journey.temp_dir,
                             'missing_directory':journey.employees_table,
                             'missing_source':journey.temp_dir,'invalid':None}[configuration]
                journey.request=Mock(return_value={'agent_md':instruction});journey.check_binding=Mock()
                if configuration=='complete':
                    self.assertEqual(journey.commit({}),journey.builder.created)
                else:
                    with self.assertRaises(ProductCaseFailure):journey.commit({})
                self.assertEqual(journey.checks[-1]['passed'],configuration=='complete')
                self.assertTrue(journey.agent_pending)

    def test_conversation_runner_checks_creator_and_new_agent_and_cleans_both(self):
        case={'id':'J2-112','title':'builder','workspace':'journey-1','executor':'api','timeout_seconds':30,
              'native_skill_builder':True,'sessions':[
              {'id':'builder','delivery_kind':'agent','turns':[{'phase':'create','prompt':'create','action':{'type':'commit_native_skill_builder'}}]},
              {'id':'published','delivery_kind':'answer','agent_from_skill':True,'turns':[{'phase':'check','prompt':'check'}]}]}
        with qa_fixture('success') as (base,_):
            execution=FunctionalExecution(base.client,case,base.evidence,base.work,'model')
            native=Mock();native.prepare.return_value={};native.selector.return_value=None
            native.builder.created={'verified':True,'agent_id':'new'}
            with patch('journey.functional_case.NativeSkillBuilderJourney',return_value=native):result=execution.run()
            self.assertEqual(result['status'],'passed');self.assertEqual(result['harness_errors'],[])
            native.commit.assert_called_once();native.check_execution.assert_called()
            self.assertEqual([c.args[1] for c in native.check_execution.call_args_list],['builder:create','published:check'])
            self.assertEqual(len(result['conversations']),2);self.assertEqual(native.cleanup_outputs.call_count,2)
            native.cleanup.assert_called_once_with(dependencies_confirmed=True)


if __name__=='__main__':unittest.main()
