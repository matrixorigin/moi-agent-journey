import copy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.client import Failure
from journey.evidence import Evidence
from journey.functional_case import FunctionalExecution
from journey.native_skill_stages import NativeSkillStages
from journey.native_skill_journey import NativeSkillJourney
from journey.native_skill_checks import business_effect
from journey.report import native_stage_summary
from test_journeys import qa_fixture
from test_native_skill import snapshot, proposed


class NativeStagesTests(unittest.TestCase):
    def case(self):
        stages=[{'id':'a','title':'prepare','depends_on':[]}, {'id':'b','title':'independent','depends_on':[]},
                {'id':'c','title':'dependent','depends_on':['a']}]
        turns=[{'stage':s,'phase':p,'prompt':prompt,'native_check':{}} for s,p,prompt in
               [('a','preview','preview'),('a','confirm','确认 {missing_change}'),('b','query','query'),('c','dependent','{missing_record}')]]
        return {'id':'J2-111','title':'stages','workspace':'journey-1','executor':'api','timeout_seconds':30,
                'native_skill_journey':{'stages':stages}, 'sessions':[{'id':'native','delivery_kind':'answer','agent_from_skill':True,
                'expected_checks':8,'turns':turns}]}

    def tracker(self, case=None):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        case=case or self.case()
        execution=SimpleNamespace(case=case,report={'status':'failed'},tasks=[],checkpoint=lambda:None,evidence=Evidence(directory.name))
        return NativeSkillStages(execution,case['native_skill_journey']['stages']),execution

    def test_failed_prerequisite_skips_missing_variables_but_runs_independent_stage_in_same_conversation(self):
        with qa_fixture('success') as (base,_):
            execution=FunctionalExecution(base.client,self.case(),base.evidence,base.work,'model')
            native=Mock()
            def construct(execution,definition,checks):
                progress=NativeSkillStages(execution,definition['stages']);native.progress=progress
                native.prepare.return_value={};native.selector.return_value=None
                native.admit.side_effect=lambda turn:progress.admit(turn,len(checks))
                native.send.side_effect=execution.send
                def evaluate(tid,cid,sid,phase,reply,spec,variables):
                    passed=phase!='preview'
                    checks.append({'session':sid,'rule':phase,'passed':passed,'detail':'' if passed else 'bad_preview'})
                    progress.complete(phase,checks,ready=passed,incomplete=False)
                    return False
                native.evaluate.side_effect=evaluate
                return native
            with patch('journey.functional_case.NativeSkillJourney',side_effect=construct):
                result=execution.run()
            self.assertEqual(result['status'],'failed');self.assertEqual(result['harness_errors'],[])
            self.assertEqual([t['phase'] for t in result['tasks']],['native:preview','native:query'])
            self.assertEqual(len({t['conversation_id'] for t in result['tasks']}),1)
            self.assertEqual(len(result['conversations']),1)
            self.assertEqual([s['status'] for s in result['native_skill_stages']],['fail','pass','dependency_blocked'])
            native.prepare.assert_called_once();native.cleanup.assert_called_once_with(dependencies_confirmed=True)
            markdown='\n'.join(native_stage_summary(result['native_skill_stages']))
            self.assertIn('依赖阻塞',markdown);self.assertIn('trace-',markdown)

    def test_reply_failure_does_not_block_confirmed_business_state(self):
        case=self.case();case['sessions'][0]['turns']=case['sessions'][0]['turns'][1:];case['sessions'][0]['turns'][0]['prompt']='done'
        tracker,execution=self.tracker(case)
        checks=[{'passed':False,'detail':'missing_confirmation_text'}]
        self.assertTrue(tracker.admit(case['sessions'][0]['turns'][0],0))
        tracker.complete('confirm',checks,ready=True,incomplete=False)
        self.assertEqual(tracker.stages['a']['status'],'fail');self.assertTrue(tracker.stages['a']['can_continue'])
        self.assertTrue(tracker.admit(case['sessions'][0]['turns'][2],len(checks)))

    def test_interrupted_stage_is_single_failure_and_future_dependencies_are_blocked(self):
        tracker,execution=self.tracker();turns=execution.case['sessions'][0]['turns']
        tracker.admit(turns[0],0);execution.report['status']='timeout'
        execution.tasks=[{'phase':'native:preview','task_id':'task','trace_id':'trace','conversation_id':'cid','state':'canceled','seconds':300}]
        tracker.finish()
        self.assertEqual(tracker.stages['a']['status'],'fail')
        self.assertEqual(tracker.stages['a']['turns'][1]['status'],'dependency_blocked')
        self.assertEqual(tracker.stages['b']['status'],'not_run')
        self.assertEqual(tracker.stages['c']['status'],'dependency_blocked')
        self.assertEqual(tracker.stages['a']['seconds'],300)

    def test_forward_dependencies_and_interleaved_stages_are_rejected(self):
        case=self.case();case['native_skill_journey']['stages'][0]['depends_on']=['c']
        with self.assertRaises(Failure):self.tracker(case)
        case=self.case();case['native_skill_journey']['stages'][0]['depends_on']=[{}]
        with self.assertRaises(Failure):self.tracker(case)

    def test_truncated_evidence_does_not_hide_wrong_reply_or_missing_required_command(self):
        for reply, command, expected in [('林青', 'query_progress', 'blocked'),
                                          ('wrong', 'query_progress', 'fail'),
                                          ('林青', 'other_command', 'fail')]:
            with self.subTest(reply=reply, command=command):
                tracker,execution=self.tracker();execution.report['failures']=[]
                tracker.admit(execution.case['sessions'][0]['turns'][0],0)
                journey=NativeSkillJourney.__new__(NativeSkillJourney)
                journey.execution=execution;journey.progress=tracker;journey.checks=[];journey.session_id='native'
                journey.skill_id='skill-id';journey.name='skill';journey.discovered=journey.downloaded=False
                journey.data=Mock();journey.data.snapshot.return_value=snapshot();journey.data.state={'employees_before':[]}
                journey.before=snapshot()
                scripts=[{'succeeded':False,'truncated':True,'objects':[], 'command':command}]
                with patch('journey.native_skill_journey.observations',return_value=(True,True,scripts)), \
                     patch('journey.native_skill_journey.business_effect',return_value=(True,[])):
                    journey.evaluate('task','conversation','native','preview',reply,
                        {'kind':'query','commands':['query_progress'],'rows':[{'owner_name':'林青'}], 'reply_contains':['林青']},{})
                self.assertEqual(tracker.stages['a']['turns'][0]['status'],expected)
        case=self.case();case['sessions'][0]['turns'][1:3]=reversed(case['sessions'][0]['turns'][1:3])
        with self.assertRaises(Failure):self.tracker(case)

    def test_real_record_ids_are_captured_only_from_verified_preview(self):
        before=snapshot();item=proposed();after=copy.deepcopy(before);after['progress_changes']=[item];after['tables'].append(item['branch_name'])
        spec={'kind':'preview','expected':[{'entity':'customer','action':'create','capture':'change','capture_record':'customer_id','fields':{'name':'customer'}}]}
        variables={};self.assertTrue(business_effect(spec,before,after,variables)[0]);self.assertEqual(variables['customer_id'],item['record_id'])
        after['progress_changes'][0]['record_id']='foreign';variables={}
        self.assertFalse(business_effect(spec,before,after,variables)[0]);self.assertEqual(variables,{})

    def test_each_turn_deadline_is_bounded_and_original_deadline_restored_on_error(self):
        journey=NativeSkillJourney.__new__(NativeSkillJourney);journey.turn_timeout=300
        client=SimpleNamespace(deadline=1000)
        journey.data=Mock();journey.execution=SimpleNamespace(client=client)
        def send(*args,**kwargs):
            self.assertEqual(client.deadline,310)
            raise Failure('deadline_exceeded')
        journey.execution.send=send
        with patch('journey.native_skill_journey.time.monotonic',return_value=10):
            with self.assertRaises(Failure):journey.send('cid','phase','prompt')
        self.assertEqual(client.deadline,1000)


if __name__=='__main__':unittest.main()
