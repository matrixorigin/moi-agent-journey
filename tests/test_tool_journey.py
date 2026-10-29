import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from journey.client import Failure
from journey.builder_flow import ProductCaseFailure
from journey.evidence import Evidence
from journey.tool_journey import ToolJourney, evaluate_http_tool


class ToolJourneyTests(unittest.TestCase):
    def test_invalid_definition_that_creates_a_tool_stops_publication_and_is_cleaned(self):
        journey, execution = self.fixture()
        journey.mode = 'invalid'
        journey.publish_agent = Mock()
        tool, stored = journey.tools[0], {}
        def product(method, path, body=None):
            if method == 'GET':
                if not stored:
                    raise Failure('http_error', 404)
                return stored.copy()
            if method == 'POST':
                stored.update({**body, 'workspace_id': 'ws', 'version': 1, 'bindable': True})
                return stored.copy()
            if method == 'DELETE':
                stored.clear()
                return {}
            raise AssertionError((method, path))
        execution.client.product.side_effect = product
        with self.assertRaisesRegex(ProductCaseFailure, 'invalid_tool_definition_created'):
            journey.prepare()
        journey.publish_agent.assert_not_called()
        self.assertEqual(journey.state['pending_ids'], [tool['id']])
        self.assertFalse(journey.checks[0]['passed'])
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'], 'completed')
        self.assertEqual(stored, {})
        posts = [call for call in execution.client.product.call_args_list if call.args[0] == 'POST']
        self.assertEqual(len(posts), 1, 'the invalid creation is not retried or overwritten')

    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        execution = SimpleNamespace(root='/workspaces/ws', case={'sessions': [{'id': 'tool'}]},
            report={'run_id': '123', 'harness_errors': []}, client=Mock(deadline=time.monotonic()+60),
            evidence=Evidence(Path(directory.name)), checkpoint=Mock())
        return ToolJourney(execution, {'mode': 'bindings'}, []), execution

    def test_cleanup_only_removes_owned_binding_and_preserves_builtin_tools(self):
        journey, execution = self.fixture()
        tool = journey.tools[0]
        journey.agent_pending = True
        agent = {'id': journey.agent_id, 'workspace_id': 'ws', 'name': journey.agent_id, 'status': 'archived'}
        execution.client.product.side_effect = [agent, agent, {}, {'tools': [{'id': 'builtin', 'workspace_id': 'system'}]}]
        journey.detach_agent_resources({tool['id']}, agent_name=journey.agent_id)
        mutations = [call.args for call in execution.client.product.call_args_list if call.args[0] == 'PATCH']
        self.assertEqual(mutations, [('PATCH', '/workspaces/ws' + journey.agent_path + '/bindings',
            {'binding_ops': [{'op': 'remove', 'kind': 'tool', 'ref': {'id': tool['id'], 'kind': 'tool', 'workspace_id': 'ws'}}]})])

    def test_cleanup_never_mutates_foreign_agent_or_tool(self):
        for foreign_agent in (True, False):
            journey, execution = self.fixture()
            tool = journey.tools[0]
            journey.state['pending_ids'] = [tool['id']]
            journey.agent_pending = foreign_agent
            bad = {'id': journey.agent_id if foreign_agent else tool['id'], 'workspace_id': 'other', 'name': 'other'}
            execution.client.product.side_effect = [bad, bad]
            journey.cleanup(dependencies_confirmed=True)
            self.assertEqual(journey.state['cleanup'], 'incomplete')
            self.assertTrue(execution.report['harness_errors'])
            self.assertTrue(all(call.args[0] == 'GET' for call in execution.client.product.call_args_list))

    def test_missing_terminal_task_proof_prevents_cleanup_mutations(self):
        journey, execution = self.fixture()
        journey.cleanup(dependencies_confirmed=False)
        execution.client.product.assert_not_called()
        self.assertEqual(journey.state['cleanup'], 'incomplete')

    def test_http_checks_require_task_call_identity_arguments_order_and_real_output(self):
        call = {'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'tool', 'call_id': 'call', 'arguments': {}}}
        result = {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'tool', 'call_id': 'call',
            'success': True, 'output': {'metadata': {'source': 'http_api'}, 'output': {'status_code': 200, 'body': '{"id":"J2-07"}'}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            def check(rows):
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
                return all(item['passed'] for item in evaluate_http_tool(path, 'task', 'session', 'tool', 200, 'J2-07'))
            self.assertTrue(check([call, result]))
            self.assertFalse(check([result, call]))
            for defect in ('task', 'call', 'tool', 'arguments', 'body', 'success', 'status'):
                c, r = copy.deepcopy(call), copy.deepcopy(result)
                if defect == 'task': r['task_id'] = 'other'
                elif defect == 'call': r['payload']['call_id'] = 'other'
                elif defect == 'tool': r['payload']['tool_id'] = 'other'
                elif defect == 'arguments': c['payload']['arguments'] = {'unexpected': 1}
                elif defect == 'body': r['payload']['output']['output']['body'] = '{"id":"wrong"}'
                elif defect == 'success': r['payload']['success'] = False
                else: r['payload']['output']['output']['status_code'] = '200'
                self.assertFalse(check([c, r]), defect)

    def test_http_404_is_an_observed_http_response_not_a_transport_failure(self):
        rows = [{'task_id': 'task', 'kind': 'tool_call', 'payload': {'tool_id': 'tool', 'call_id': 'call', 'arguments': {}}},
                {'task_id': 'task', 'kind': 'tool_result', 'payload': {'tool_id': 'tool', 'call_id': 'call', 'success': True,
                    'output': {'metadata': {'source': 'http_api'}, 'output': {'status_code': 404, 'body': '404: Not Found'}}}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            self.assertTrue(all(item['passed'] for item in evaluate_http_tool(path, 'task', 'session', 'tool', 404)))

    def test_http_error_metrics_preserve_observed_failure_and_mark_only_expected_404(self):
        from journey.diagnostics import analyze_events, _output_observations
        from journey.report import mark_expected_tool_errors
        rows = [{'task_id': 'task', 'seq': 1, 'kind': 'tool_call', 'payload': {'tool_id': 'tool', 'call_id': 'call', 'arguments': {}}},
                {'task_id': 'task', 'seq': 2, 'kind': 'tool_result', 'payload': {'tool_id': 'tool', 'call_id': 'call', 'success': True,
                 'output': {'metadata': {'source': 'http_api'}, 'output': {'status_code': 404, 'body': 'Not Found'}}}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            checks = evaluate_http_tool(path, 'task', 'session', 'tool', 404)
            metrics = analyze_events(path, [{'task_id': 'task', 'phase': 'tool:missing', 'event_count': 2}])
            mark_expected_tool_errors(metrics, checks)
            self.assertEqual(len(metrics['errors']), 1)
            self.assertEqual(metrics['errors'][0]['arguments'], {})
            self.assertTrue(metrics['errors'][0]['errors'][0]['expected'])
        for status in (200, '404', True):
            self.assertEqual(_output_observations({'metadata': {'source': 'http_api'}, 'output': {'status_code': status}}), ([], False))
        self.assertEqual(_output_observations({'output': {'status_code': 404, 'body': 'application data'}}), ([], False))
