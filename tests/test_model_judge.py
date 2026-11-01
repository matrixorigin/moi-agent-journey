import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_knowledge_journey as fixtures
from journey.client import Failure
from journey.evidence import Evidence
from journey.model_judge import evaluate_model, object_schema
from journey.rag_quality import judge_data, judge_schema, parse_verdict, summarize_verdict
from journey.rag_report import summarize_rag, valid_rag_result
from journey.run_case import Execution


class StructuredModelTests(unittest.TestCase):
    def execution(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        client=Mock();client.request.return_value={'id':'model-call','model':'actual-model',
            'usage':{'total_tokens':12},'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]}
        return SimpleNamespace(root='/workspaces/owned',model='chosen-model',report={},
            client=client,evidence=Evidence(temp.name))

    def test_existing_workspace_route_gets_strict_schema_and_no_agent_or_tool_task(self):
        ex=self.execution();schema=object_schema({'ok':{'type':'boolean'}})
        self.assertEqual(evaluate_model(ex,'test','Evaluate this',schema),'{"ok":true}')
        method,path,body=ex.client.request.call_args.args
        self.assertEqual((method,path),('POST','/workspaces/owned/llm/chat/completions?service_slot=agent_chat'))
        self.assertEqual(body['response_format']['json_schema'],{'name':'journey_evaluation','strict':True,'schema':schema})
        self.assertNotIn('tools',body)
        self.assertEqual(body['reasoning_effort'],'low')
        self.assertEqual(body['max_tokens'],16384)
        self.assertEqual(ex.report['model_evaluations'][0]['id'],'model-call')
        self.assertTrue((ex.evidence.root/'evaluations/test/response.json').exists())
        ex.client.rpc.assert_not_called()

    def test_incomplete_malformed_and_tool_responses_fail_once_with_original_response_retained(self):
        for kind in ['length','json','tools','refusal']:
            ex=self.execution();response=ex.client.request.return_value
            choice=response['choices'][0]
            if kind=='length':choice['finish_reason']='length'
            elif kind=='json':choice['message']['content']='{"ok":'
            elif kind=='tools':choice['message']['tool_calls']=[{'name':'search'}]
            else:choice['message']['refusal']='refused'
            with self.assertRaises((Failure,ValueError)):
                evaluate_model(ex,'test','Evaluate',object_schema({'ok':{'type':'boolean'}}))
            self.assertEqual(ex.client.request.call_count,1)
            self.assertEqual(ex.report['model_evaluations'][0]['state'],'error')
            self.assertEqual(json.loads((ex.evidence.root/'evaluations/test/response.json').read_text()),response)

    def test_schema_limits_ids_and_forces_unavailable_citation_to_null(self):
        obs,eff,ref,_=fixtures.RagEvidenceTests().verdict_fixture()
        obs['citation_chunks'][0]['content']='z'*70_000
        obs['retrieved_chunks'][0]['content']='z'*70_000
        data=judge_data('How long?','45 days',ref,obs,eff)
        schema=judge_schema(data,obs)
        citation=schema['properties']['citations']['items']['anyOf'][0]['properties']
        self.assertEqual(citation['supported'],{'type':'null'})
        self.assertEqual(citation['source_ids']['maxItems'],0)
        self.assertEqual(schema['properties']['criteria']['items']['properties']['id']['enum'],['C01'])
        self.assertFalse(schema['additionalProperties'])

    def test_bad_efficiency_does_not_discard_valid_answer_and_citation_scores(self):
        obs,eff,ref,v=fixtures.RagEvidenceTests().verdict_fixture()
        v['efficiency']={'assessment':'concerns','summary':'wrong trace ID','findings':[
            {'kind':'repetition','reason':'repeat','call_ids':['invented'],'avoidable':True}]}
        text=json.dumps(v)
        with self.assertRaisesRegex(ValueError,'efficiency_finding'):
            parse_verdict(text,'45 days',ref,obs,eff)
        result=parse_verdict(text,'45 days',ref,obs,eff,allow_partial=True)
        self.assertIn('efficiency',result['dimension_errors'])
        self.assertEqual(summarize_verdict(result,obs)['correctness']['rate'],1)
        self.assertEqual(summarize_verdict(result,obs)['citation_support']['rate'],1)
        self.assertEqual(json.loads(text),v)

    def test_invalid_criteria_are_unscored_while_other_dimensions_and_denominators_survive(self):
        obs,eff,ref,v=fixtures.RagEvidenceTests().verdict_fixture()
        v['criteria'][0]['id']='invented'
        result=parse_verdict(json.dumps(v),'45 days',ref,obs,eff,allow_partial=True)
        metrics=summarize_verdict(result,obs)
        self.assertIsNone(metrics['correctness']['rate'])
        self.assertEqual(metrics['correctness']['unavailable'],1)
        self.assertEqual(metrics['citation_support']['rate'],1)
        answer={'session':'qa','phase':'question','question':'How long?','text':'45 days','task_id':'t',
                'evaluation_state':'partial','metrics':metrics,'verdict':result}
        rag={'corpus':'test','expected_answers':1,'answers':[answer]}
        self.assertTrue(valid_rag_result(rag))
        summary=summarize_rag([{'case_id':'test','rag':rag}])
        self.assertEqual(summary['evaluation_states'],{'partial':1})
        self.assertEqual(summary['metrics']['correctness']['total'],0)
        self.assertEqual(summary['metrics']['correctness']['unavailable'],1)

    def test_pi_grader_resolves_output_ids_to_exact_quotes_including_punctuation(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Evidence(directory)
            output=evidence.root/'sessions/plan/outputs';output.mkdir(parents=True)
            content='Use “approved” — exactly.\nSecond line.'
            (output/'plan.md').write_text(content)
            (output.parent/'reply.json').write_text(json.dumps({'text':'Delivered.'}))
            case={'id':'test','title':'test','workspace':'journey-1','timeout_seconds':60,
                  'assets':[],'sessions':[{'id':'plan','rubric':['The plan includes approved.']}]}
            ex=Execution(SimpleNamespace(requests=0),case,evidence,directory,'model')
            ex.report['sessions']=[{'id':'plan'}]
            verdict={'checks':[{'id':'C01','passed':True,'reason':'present','answer_ids':['O1_1']}]}
            with patch('journey.model_judge.evaluate_model',return_value=json.dumps(verdict)) as model:
                ex.judge_pi()
            self.assertTrue(ex.report['semantic_judge']['passed'])
            result=json.loads((evidence.root/'semantic-verdicts.json').read_text())
            self.assertEqual(result[0]['checks'][0]['evidence_quote'],content)
            self.assertEqual(result[0]['checks'][0]['evidence_quotes'],[{'file':'plan.md','quote':content}])
            self.assertNotIn('evidence_quote',model.call_args.args[3]['properties']['checks']['items']['properties'])
            verdict['checks'][0]['answer_ids']=['invented']
            with patch('journey.model_judge.evaluate_model',return_value=json.dumps(verdict)):
                with self.assertRaisesRegex(Failure,'invalid_semantic_judge_verdict'):
                    ex.judge_pi()
