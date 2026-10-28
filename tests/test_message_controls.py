import unittest
from types import SimpleNamespace

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.message_controls import check_completed_cancellation, check_empty_message


class MessageClient:
    def __init__(self, mode):
        self.mode = mode
        self.items = [{'id': 'task-1', 'context_id': 'conversation'}]
        self.calls = 0

    def product(self, _method, _path, _body=None):
        return {'items': list(self.items), 'total': len(self.items)}

    def rpc(self, _selector, _method, params):
        self.calls += 1
        assert params['message']['parts'] == []
        if self.mode == 'rejected':
            raise Failure('http_error', 400)
        if self.mode == 'network':
            raise Failure('transport_error')
        if self.mode == 'accepted_task':
            self.items.append({'id': 'task-2', 'context_id': 'conversation'})
        return {'kind': 'task'}


def execution(client):
    return SimpleNamespace(client=client, root='/workspaces/workspace', model='model', report={},
        evidence=SimpleNamespace(write=lambda *_: None, append=lambda *_: None), checkpoint=lambda: None,
        conversation_selector=lambda _: {'agent_id': 'agent', 'agent_workspace_id': 'system'},
        tasks=[{'conversation_id': 'conversation', 'task_id': 'task-1'}], pending_conversation=None)


class MessageControlTests(unittest.TestCase):
    def test_rejected_empty_message_preserves_the_existing_task(self):
        client = MessageClient('rejected')
        runner, checks = execution(client), []
        check_empty_message(runner, 'conversation', 'test', checks)
        self.assertEqual([item['passed'] for item in checks], [True, True, True])
        self.assertIsNone(runner.pending_conversation)
        self.assertEqual(client.calls, 1)

    def test_accidentally_admitted_task_is_not_ignored_or_resent(self):
        client = MessageClient('accepted_task')
        runner, checks = execution(client), []
        with self.assertRaises(ProductCaseFailure):
            check_empty_message(runner, 'conversation', 'test', checks)
        self.assertEqual([item['passed'] for item in checks], [True, False, False])
        self.assertEqual(runner.pending_conversation, 'conversation')
        self.assertEqual(client.calls, 1)

    def test_network_error_keeps_admission_outcome_unknown(self):
        client = MessageClient('network')
        runner = execution(client)
        with self.assertRaisesRegex(Failure, 'transport_error'):
            check_empty_message(runner, 'conversation', 'test', [])
        self.assertEqual(runner.pending_conversation, 'conversation')
        self.assertEqual(client.calls, 1)

    def test_task_list_missing_the_actual_task_cannot_pass(self):
        client = MessageClient('rejected')
        client.items = []
        checks = []
        with self.assertRaises(ProductCaseFailure):
            check_empty_message(execution(client), 'conversation', 'test', checks)
        self.assertFalse(checks[0]['passed'])
        self.assertEqual(client.calls, 0)

    def test_late_cancellation_preserves_the_completed_answer(self):
        task = {'kind': 'task', 'id': 'task-1', 'contextId': 'conversation', 'status': {
            'state': 'completed', 'message': {'role': 'agent', 'parts': [{'kind': 'text', 'text': 'answer'}]}}}
        for changed in (False, True):
            with self.subTest(changed=changed):
                class Client:
                    def rpc(self, _selector, method, params):
                        self.assert_id = params['id']
                        if method == 'tasks/cancel':
                            return task
                        if changed:
                            return {**task, 'status': {'state': 'canceled'}}
                        return task
                client = Client()
                checks = []
                check_completed_cancellation(execution(client), 'conversation', 'test', task, checks)
                self.assertEqual(client.assert_id, 'task-1')
                self.assertEqual([check['passed'] for check in checks],
                                 [True, not changed, not changed])

    def test_late_cancellation_rejection_cannot_pass_but_task_is_rechecked(self):
        task = {'kind': 'task', 'id': 'task-1', 'contextId': 'conversation', 'status': {
            'state': 'completed', 'message': {'role': 'agent', 'parts': [{'kind': 'text', 'text': 'answer'}]}}}
        class Client:
            def rpc(self, _selector, method, _params):
                if method == 'tasks/cancel':
                    raise Failure('http_error', 409)
                return task
        checks = []
        check_completed_cancellation(execution(Client()), 'conversation', 'test', task, checks)
        self.assertEqual([check['passed'] for check in checks], [False, True, True])


if __name__ == '__main__':
    unittest.main()
