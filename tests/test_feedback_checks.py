import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence
from journey.feedback_checks import check_feedback


class FeedbackClient:
    def __init__(self, *, accepts_missing=False, wrong_saved=False, network=False, creates_task=False):
        self.accepts_missing, self.wrong_saved = accepts_missing, wrong_saved
        self.network, self.creates_task = network, creates_task
        self.saved, self.last_receipt, self.sends, self.tasks = {}, None, 0, ['task']

    def product(self, _method, path, _body=None):
        if '/agent-runtime-tasks?' in path:
            return {'items': [{'id': item, 'context_id': 'conversation'} for item in self.tasks],
                    'total': len(self.tasks)}
        if '/feedback/stats?' in path:
            query = parse_qs(urlsplit(path).query)
            assert query['message_id'] == ['assistant']
            items = self.saved.get('assistant', [])
            return {'total': len(items), 'by_rating': {
                key: sum(item['rating'] == key for item in items) for key in ('up', 'down', 'neutral')}}
        if '/feedback?' in path:
            query = parse_qs(urlsplit(path).query)
            target = query['message_id'][0]
            items = self.saved.get(target, [])
            if 'rating' in query:
                items = [item for item in items if item['rating'] == query['rating'][0]]
            return {'items': items, 'total': len(items)}
        raise AssertionError(path)

    def rpc(self, _selector, method, params):
        assert method == 'message/send'
        self.sends += 1
        message = params['message']
        data = message['parts'][0]['data']
        target_id = data['target']['messageId']
        if target_id.startswith('missing-'):
            if self.creates_task:
                self.tasks.append('unexpected')
            if not self.accepts_missing:
                raise Failure('http_error', 400)
        elif self.network:
            raise Failure('transport_error')
        if target_id == 'assistant' and self.last_receipt:
            return self.last_receipt
        target = {'agentId': 'agent', 'contextId': 'conversation', 'taskId': 'task', 'messageId': target_id}
        receipt = {'kind': 'message', 'role': 'agent', 'contextId': 'conversation',
                   'messageId': message['messageId'], 'parts': [{'kind': 'data', 'data': {
                       'type': 'moi.feedback.received', 'version': 1, 'feedbackId': data['feedbackId'],
                       'status': 'recorded', 'target': target}}]}
        row = {'id': data['feedbackId'], 'workspace_id': 'workspace', 'agent_id': 'agent',
               'context_id': 'conversation', 'task_id': 'task', 'message_id': target_id,
               'rating': 'down' if self.wrong_saved else data['rating'], 'intent': 'record_only'}
        for key in ('comment', 'labels'):
            if key in data:
                row[key] = data[key]
        self.saved.setdefault(target_id, []).append(row)
        if target_id == 'assistant':
            self.last_receipt = receipt
        return receipt


class FeedbackChecksTests(unittest.TestCase):
    def execution(self, **options):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        client = FeedbackClient(**options)
        runner = SimpleNamespace(client=client, root='/workspaces/workspace', evidence=Evidence(Path(tmp.name)),
            report={}, checkpoint=lambda: None, pending_conversation=None,
            conversation_selector=lambda _: {'agent_id': 'agent', 'agent_workspace_id': 'system'},
            tasks=[{'task_id': 'task', 'conversation_id': 'conversation'}])
        task = {'id': 'task', 'contextId': 'conversation', 'status': {'state': 'completed', 'message': {
            'role': 'agent', 'messageId': 'assistant', 'taskId': 'task', 'contextId': 'conversation'}}}
        return runner, task

    def test_feedback_validates_target_persistence_and_intentional_replay(self):
        runner, task = self.execution()
        checks = []
        check_feedback(runner, 'conversation', 'session', task, checks)
        self.assertEqual([check['passed'] for check in checks], [True] * 7)
        self.assertEqual(runner.client.sends, 3)
        self.assertIsNone(runner.pending_conversation)

    def test_accepted_unknown_target_and_wrong_saved_rating_are_failures(self):
        runner, task = self.execution(accepts_missing=True, wrong_saved=True)
        checks = []
        check_feedback(runner, 'conversation', 'session', task, checks)
        self.assertEqual([check['passed'] for check in checks], [True, False, False, True, False, True, True])

    def test_unknown_outcome_is_not_retried(self):
        runner, task = self.execution(network=True)
        with self.assertRaises(Failure):
            check_feedback(runner, 'conversation', 'session', task, [])
        self.assertEqual(runner.client.sends, 2)
        self.assertEqual(runner.pending_conversation, 'conversation')

    def test_negative_feedback_persists_comment_labels_and_filters(self):
        runner, task = self.execution()
        checks = []
        check_feedback(runner, 'conversation', 'session', task, checks, rating='down',
                       comment='citation needs review', labels=['citation', 'quality'], check_filter=True)
        self.assertTrue(all(item['passed'] for item in checks))
        saved = runner.client.saved['assistant'][0]
        self.assertEqual((saved['rating'], saved['comment'], saved['labels']),
                         ('down', 'citation needs review', ['citation', 'quality']))

    def test_accidental_task_is_left_for_cleanup(self):
        runner, task = self.execution(creates_task=True)
        with self.assertRaises(ProductCaseFailure):
            check_feedback(runner, 'conversation', 'session', task, [])
        self.assertEqual(runner.client.sends, 1)
        self.assertEqual(runner.pending_conversation, 'conversation')

    def test_neutral_stats_are_answer_scoped_and_replay_does_not_increase_them(self):
        runner, task = self.execution()
        checks = []
        check_feedback(runner, 'conversation', 'session', task, checks, rating='neutral',
                       check_filter=True, check_stats=True)
        self.assertEqual(len(checks), 12)
        self.assertTrue(all(item['passed'] for item in checks))


if __name__ == '__main__':
    unittest.main()
