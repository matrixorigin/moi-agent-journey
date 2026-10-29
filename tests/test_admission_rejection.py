import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from journey.client import Failure
from journey.evidence import Evidence
from journey.run_case import Execution
from journey.outcomes import assess


class AdmissionRejectionTests(unittest.TestCase):
    def execution(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        case = {'id': 'J2-113', 'workspace': 'journey-2', 'title': 'admission rejection', 'executor': 'api',
                'timeout_seconds': 300, 'sessions': [{'id': 'live', 'turns': [{'phase': 'baseline'}, {'phase': 'after'}]}]}
        execution = Execution(Mock(requests=0), case, Evidence(Path(directory.name)), directory.name, 'model')
        execution.root = '/workspaces/ws'
        execution.pending_conversation = 'conversation'
        execution.pending_rejection = {'conversation_id': 'conversation', 'phase': 'live:after',
            'error': {'code': -32603, 'data': {'domain': 'moi-core.agent_runtime', 'reason': 'INTERNAL'}}}
        execution.tasks.append({'task_id': 'baseline', 'conversation_id': 'conversation',
                               'phase': 'live:baseline', 'state': 'completed'})
        execution.conversations.append({'id': 'conversation', 'selector': {'agent_id': 'agent', 'agent_workspace_id': 'ws'}})
        execution.capture = Mock()
        listing = {'total': 1, 'items': [{'id': 'baseline', 'context_id': 'conversation', 'agent_id': 'agent',
                                         'agent_workspace_id': 'ws', 'a2a_state': 'completed'}]}
        conversation = {'id': 'conversation', 'agent_id': 'agent', 'agent_workspace_id': 'ws',
                        'latest_task_id': 'baseline', 'latest_task_state': 'completed'}
        return execution, listing, conversation

    def test_explicit_rejection_and_consistent_idle_facts_allow_cleanup(self):
        execution, listing, conversation = self.execution()
        execution.client.product.side_effect = [listing, conversation]
        execution.cleanup_tasks()
        self.assertIsNone(execution.pending_conversation)
        self.assertEqual(execution.report['harness_errors'], [])
        self.assertEqual(execution.report['admission_failures'][0]['phase'], 'live:after')
        proof = execution.evidence.root / 'admission-rejections/conversation.json'
        self.assertIn('confirmed_no_new_task', proof.read_text())
        self.assertEqual([t['task_id'] for t in execution.tasks], ['baseline'])

    def test_absence_alone_never_resolves_a_lost_response(self):
        execution, listing, _ = self.execution()
        execution.pending_rejection = None
        execution.client.product.return_value = listing
        execution.cleanup_tasks()
        self.assertEqual(execution.pending_conversation, 'conversation')
        self.assertEqual(execution.report['harness_errors'][0]['reason'], 'send_outcome_unknown')

    def test_conflicting_or_incomplete_facts_prevent_cleanup(self):
        for defect in ('page', 'running', 'foreign_agent', 'foreign_context', 'busy', 'unknown_latest', 'wrong_conversation'):
            execution, listing, conversation = self.execution()
            if defect == 'page': listing['total'] = 2
            elif defect == 'running': listing['items'][0]['a2a_state'] = 'working'
            elif defect == 'foreign_agent': listing['items'][0]['agent_id'] = 'other'
            elif defect == 'foreign_context': listing['items'][0]['context_id'] = 'other'
            elif defect == 'busy': conversation['active_task_id'] = 'new-task'
            elif defect == 'unknown_latest': conversation['latest_task_id'] = 'new-task'
            else: conversation['id'] = 'other'
            execution.client.product.side_effect = [listing, conversation]
            execution.cleanup_tasks()
            self.assertEqual(execution.pending_conversation, 'conversation', defect)
            self.assertTrue(execution.report['harness_errors'], defect)
            self.assertNotIn('admission_failures', execution.report)

    def test_admitted_task_after_error_is_recovered_not_discarded(self):
        execution, listing, _ = self.execution()
        listing['items'].append({**listing['items'][0], 'id': 'new-task', 'a2a_state': 'failed'})
        listing['total'] = 2
        execution.client.product.return_value = listing
        execution.cleanup_tasks()
        self.assertIsNone(execution.pending_conversation)
        self.assertEqual([t['task_id'] for t in execution.tasks], ['baseline', 'new-task'])
        self.assertNotIn('admission_failures', execution.report)

    def test_rejection_is_a_product_failure_even_when_prior_turn_succeeded(self):
        execution, _, _ = self.execution()
        execution.report['stage'] = 'live:after'
        execution.record_failure(Failure('agent_runtime_admission_error', details=execution.pending_rejection['error']))
        self.assertEqual(execution.report['status'], 'failed')
        self.assertEqual(execution.report['harness_errors'], [])
        _, agent, _, _ = assess(execution.case, execution.report, 'failed')
        self.assertEqual(agent['state'], 'abnormal')


if __name__ == '__main__':
    unittest.main()
