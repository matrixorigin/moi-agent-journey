import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence
from journey.hello import discover
from journey.message_admission import MessageAdmissionJourney, command_outputs, rpc_rejection
from journey.published_conflict import PublishedConflictJourney
from test_builder_flow import task
from test_journeys import qa_fixture


def rejection_exchange():
    return {'request': {'id': 'rpc'}, 'status': 500, 'headers': {'X-Trace-Id': 'trace'},
        'response': {'error_body': json.dumps({'jsonrpc': '2.0', 'id': 'rpc', 'error': {
            'code': -32603, 'data': {'domain': 'moi-core.agent_runtime', 'reason': 'INTERNAL'}}})}}


class MessageAdmissionTests(unittest.TestCase):
    def journey(self, mode='replay'):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        execution = SimpleNamespace(root='/workspaces/ws', model='model',
            case={'sessions': [{'id': 'main'}]}, report={'run_id': 'unique', 'harness_errors': []}, tasks=[],
            client=Mock(deadline=time.monotonic()+60, headers={'X-API-Key': 'private'}, requests=0),
            evidence=Evidence(directory.name), checkpoint=Mock(), pending_conversation=None, pending_rejection=None,
            conversation_selector=Mock(return_value={'agent_id': 'agent', 'agent_workspace_id': 'ws'}))
        journey = MessageAdmissionJourney(execution, mode, [])
        journey.prepare()
        return journey, execution

    def test_only_matching_structured_rejection_proves_non_admission(self):
        good = rejection_exchange()
        self.assertEqual(rpc_rejection(good)['code'], -32603)
        defects = [copy.deepcopy(good) for _ in range(4)]
        defects[0]['request']['id'] = 'foreign'
        defects[1]['response']['error_body'] = '<html>upstream error</html>'
        for item, field, value in [(defects[2], 'domain', 'other'), (defects[3], 'reason', 'ADMISSION_TIMEOUT')]:
            body = json.loads(item['response']['error_body'])
            body['error']['data'][field] = value
            item['response']['error_body'] = json.dumps(body)
        for item in defects:
            self.assertIsNone(rpc_rejection(item))

    def test_transport_loss_retains_pending_identity_and_never_retries(self):
        journey, execution = self.journey()
        child = Mock(requests=1)
        child.rpc.side_effect = Failure('transport_error')
        params = {'message': {'contextId': 'cid', 'messageId': 'message'}}
        with patch('journey.message_admission.Client', return_value=child), self.assertRaisesRegex(Failure, 'transport_error'):
            journey.control(params, 'test')
        self.assertEqual(execution.pending_conversation, 'cid')
        self.assertIsNone(execution.pending_rejection)
        child.rpc.assert_called_once()

    def test_explicit_500_keeps_pending_until_terminal_task_and_idle_conversation_can_be_checked(self):
        journey, execution = self.journey('busy')
        child = Mock(requests=1)
        def factory(*args, **kwargs):
            kwargs['observer'](rejection_exchange())
            return child
        child.rpc.side_effect = Failure('http_error', 500)
        params = {'message': {'contextId': 'cid', 'messageId': 'message'}}
        with patch('journey.message_admission.Client', side_effect=factory):
            response, error = journey.control(params, 'test')
        self.assertIsNone(response)
        self.assertEqual(error['trace_headers'], {'X-Trace-Id': 'trace'})
        self.assertEqual(execution.pending_conversation, 'cid')
        self.assertEqual(execution.pending_rejection['error']['data']['reason'], 'INTERNAL')
        execution.cleanup_tasks = Mock()
        with self.assertRaisesRegex(Failure, 'probe_admission_outcome_unconfirmed'):
            journey.confirm_control('cid')
        execution.cleanup_tasks.assert_called_once()

    def test_finished_task_does_not_masquerade_as_a_concurrent_probe(self):
        journey, execution = self.journey('busy')
        execution.client.rpc.return_value = {'kind': 'task', 'id': 'task', 'contextId': 'cid', 'status': {'state': 'completed'}}
        with self.assertRaises(ProductCaseFailure):
            journey.admitted({'conversation_id': 'cid', 'task_id': 'task'}, {'message': {'contextId': 'cid'}})
        self.assertTrue(execution.report['blocked'])
        self.assertFalse(journey.checks[0]['passed'])

    def test_unconfirmed_sandbox_cleanup_task_never_counts_as_completed_cleanup(self):
        journey, execution = self.journey()
        def incomplete(owner, cid):
            owner.state['sandbox_cleanup'] = {cid: {'status': 'incomplete', 'tasks_confirmed': False}}
        with patch('journey.message_admission.cleanup_sandbox', side_effect=incomplete), \
             patch('journey.message_admission.cleanup_artifacts') as artifacts:
            journey.cleanup_outputs('cid')
        artifacts.assert_not_called()
        self.assertEqual(journey.state['cleanup'], 'incomplete')
        self.assertEqual(execution.report['harness_errors'][0]['reason'], 'admission_sandbox_cleanup_unconfirmed')

    def test_duplicate_user_message_is_detected_even_when_task_count_is_one(self):
        journey, execution = self.journey()
        journey.expected = {'task': 'original prompt'}
        user = {'id': 'first', 'role': 'user', 'conversation_id': 'cid', 'task_id': 'task',
                'parts': [{'kind': 'text', 'text': 'original prompt'}]}
        execution.client.product.side_effect = [{'total': 2, 'items': [user, {**user, 'id': 'duplicate'}]},
            {'total': 1, 'items': [{**user, 'id': 'reply', 'role': 'agent'}]}]
        with patch('journey.message_admission.conversation_task_ids', return_value={'task'}):
            journey.ledger('cid')
        self.assertTrue(journey.checks[0]['passed'])
        self.assertFalse(journey.checks[1]['passed'])

    def test_duplicate_matching_write_command_never_passes(self):
        journey, execution = self.journey()
        def events(count):
            rows = []
            for index in range(count):
                common = {'task_id': 'task', 'payload': {'tool_id': 'mcp__moi-tools__bash', 'call_id': str(index)}}
                rows += [{**common, 'kind': 'tool_call', 'payload': {**common['payload'],
                    'arguments': {'command': journey.command}}}, {**common, 'kind': 'tool_result',
                    'payload': {**common['payload'], 'success': True, 'output': {'output': {'exit_code': 0, 'stdout': journey.code}}}}]
            path = execution.evidence.root/'events.jsonl'
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return command_outputs(path, 'task', journey.command)[0]
        self.assertTrue(events(1))
        self.assertFalse(events(2))

    def test_real_send_calls_admission_hook_once_before_event_drain(self):
        with qa_fixture('success') as (execution, _):
            execution.root, execution.selector = discover(execution.client, execution.case['workspace'])
            cid = execution.conversation('hook')
            calls = []
            def admitted(entry, params):
                calls.append((entry['task_id'], params['message']['messageId']))
                self.assertIs(execution.tasks[-1], entry)
                self.assertEqual(entry['event_count'], 0)
                self.assertIsNone(execution.pending_conversation)
            entry, _ = execution.send(cid, 'test', 'hello', on_admitted=admitted)
            self.assertEqual(calls, [(entry['task_id'], entry['message_id'])])
            self.assertTrue(entry['capture_complete'])


class PublishedConflictTests(unittest.TestCase):
    def journey(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        execution = SimpleNamespace(root='/workspaces/ws', case={'sessions': [{'id': 'editor'}]},
            report={'run_id': 'unique'}, evidence=Evidence(directory.name), checkpoint=Mock(), client=Mock())
        journey = PublishedConflictJourney(execution, [])
        journey.builder = SimpleNamespace(revisions={})
        return journey, execution

    def test_only_409_counts_as_stale_edit_rejection(self):
        for status, passed in [(409, True), (500, False), (400, False)]:
            with self.subTest(status=status):
                journey, execution = self.journey()
                execution.client.product.side_effect = Failure('http_error', status)
                journey.propose({}, reject=True)
                self.assertEqual(journey.checks[-1]['passed'], passed)
                self.assertFalse(journey.state['candidate_proposal_pending'])

    def test_current_edit_rejection_is_failed_check_and_500_still_retains_uncertain_outcome(self):
        for status, pending in [(400, False), (500, True)]:
            with self.subTest(status=status):
                journey, execution = self.journey()
                execution.client.product.side_effect = Failure('http_error', status)
                with self.assertRaises(ProductCaseFailure):
                    journey.propose({})
                self.assertFalse(journey.checks[-1]['passed'])
                self.assertEqual(journey.state['candidate_proposal_pending'], pending)

    def test_unexpected_stale_candidate_is_owned_for_cleanup_and_reported_as_failure(self):
        journey, execution = self.journey()
        candidate = task(journey.agent_id, 'v2', agent_id=journey.agent_id, mode='revise')['status']['message']['parts'][0]['data']['output']['output']
        execution.client.product.return_value = candidate
        journey.propose({}, reject=True)
        self.assertFalse(journey.checks[-1]['passed'])
        self.assertEqual(journey.builder.revisions[journey.agent_id], (candidate, journey.agent_id))
        self.assertFalse(journey.state['candidate_proposal_pending'])

    def test_unknown_proposal_outcome_is_never_retried_or_claimed_clean(self):
        journey, execution = self.journey()
        execution.client.product.side_effect = Failure('transport_error')
        with self.assertRaises(Failure):
            journey.propose({}, reject=True)
        self.assertTrue(journey.state['candidate_proposal_pending'])
        execution.client.product.assert_called_once()
