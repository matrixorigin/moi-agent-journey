import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.conversation_checks import check_history_and_invalid_update, check_lifecycle
from journey.functional_case import check_reply


class ConversationClient:
    def __init__(self, *, lose_archive=False, duplicate_page=False, accept_invalid=False):
        self.state = {'id': 'conversation', 'workspace_id': 'workspace', 'agent_id': 'agent', 'agent_workspace_id': 'system',
                      'status': 'active', 'title': 'initial', 'pinned': False, 'title_user_edited': False}
        self.lose_archive = lose_archive
        self.duplicate_page = duplicate_page
        self.accept_invalid = accept_invalid
        self.messages = [
            {'id': 'stored-1', 'task_id': 'task-1', 'role': 'user', 'conversation_id': 'conversation',
             'parts': [{'kind': 'text', 'text': 'first prompt'}]},
            {'id': 'agent-1', 'task_id': 'task-1', 'role': 'agent', 'conversation_id': 'conversation'},
            {'id': 'stored-2', 'task_id': 'task-2', 'role': 'user', 'conversation_id': 'conversation',
             'parts': [{'kind': 'text', 'text': 'second prompt'}]},
            {'id': 'agent-2', 'task_id': 'task-2', 'role': 'agent', 'conversation_id': 'conversation'},
        ]

    def product(self, method, path, body=None):
        url = urlsplit(path)
        query = parse_qs(url.query)
        if url.path.endswith('/messages'):
            items = [message for message in self.messages if message['role'] == query['role'][0]]
            offset, limit = int(query['offset'][0]), int(query['limit'][0])
            if self.duplicate_page and offset == 1:
                offset = 0
            return {'items': items[offset:offset + limit], 'total': len(items)}
        if url.path.endswith('/conversations'):
            matches = all(str(self.state.get(key, '')).lower() == value[0]
                          for key, value in query.items() if key in {'status', 'pinned'})
            matches = matches and query.get('keyword', [self.state['title']])[0] == self.state['title']
            return {'items': [dict(self.state)] if matches else []}
        if method == 'GET':
            return dict(self.state)
        if method == 'PATCH':
            if body.get('status') == 'invalid-status':
                if not self.accept_invalid:
                    raise Failure('http_error', 400)
                self.state.update(body)
                return dict(self.state)
            if not (self.lose_archive and body.get('status') == 'archived'):
                self.state.update(body)
            if 'title' in body:
                self.state['title_user_edited'] = True
            return dict(self.state)
        raise AssertionError((method, path))


def execution(client):
    return SimpleNamespace(client=client, root='/workspaces/workspace', report={},
        evidence=SimpleNamespace(write=lambda *_: None),
        conversation_selector=lambda _: {'agent_id': 'agent', 'agent_workspace_id': 'system'},
        checkpoint=lambda: None,
        tasks=[{'conversation_id': 'conversation', 'message_id': 'user-1', 'task_id': 'task-1'},
               {'conversation_id': 'conversation', 'message_id': 'user-2', 'task_id': 'task-2'}])


class ConversationChecksTests(unittest.TestCase):
    def test_lifecycle_checks_real_readback_and_list_membership(self):
        for lose_archive in (False, True):
            with self.subTest(lose_archive=lose_archive):
                checks = []
                check_lifecycle(execution(ConversationClient(lose_archive=lose_archive)),
                                'conversation', 'lifecycle', checks, 'unique-title')
                self.assertEqual(len(checks), 7)
                self.assertEqual(all(item['passed'] for item in checks), not lose_archive)
                if lose_archive:
                    self.assertIn('conversation_archive_mismatch', [item['detail'] for item in checks])
                    self.assertIn('archived_list_mismatch', [item['detail'] for item in checks])

    def test_history_detects_duplicate_pages_and_invalid_mutation(self):
        for mode in ('success', 'duplicate', 'invalid'):
            with self.subTest(mode=mode):
                checks = []
                client = ConversationClient(duplicate_page=mode == 'duplicate', accept_invalid=mode == 'invalid')
                check_history_and_invalid_update(execution(client), 'conversation', 'history', checks, 'unique-title',
                                                 ['first prompt', 'second prompt'])
                self.assertEqual(len(checks), 6)
                failures = [item['detail'] for item in checks if not item['passed']]
                if mode == 'success':
                    self.assertEqual(failures, [])
                elif mode == 'duplicate':
                    self.assertIn('conversation_message_pagination_mismatch', failures)
                else:
                    self.assertIn('invalid_update_status:accepted', failures)
                    self.assertIn('invalid_update_mutated_conversation', failures)

    def test_wrong_conversation_identity_stops_mutations(self):
        client = ConversationClient()
        client.state['agent_id'] = 'other-agent'
        checks = []
        with self.assertRaises(ProductCaseFailure):
            check_lifecycle(execution(client), 'conversation', 'lifecycle', checks, 'unique-title')
        self.assertEqual(client.state['title'], 'initial')
        self.assertFalse(checks[0]['passed'])

    def test_json_checks_validate_syntax_values_and_types(self):
        turn = {'phase': 'reply', 'json_fields': {'code': '{code}', 'enabled': True, 'count': 3}}
        good = check_reply('json', turn, '{"code":"ABC","enabled":true,"count":3}', {'code': 'ABC'})
        self.assertTrue(all(check['passed'] for check in good))
        for reply in ('not JSON', '{"code":"ABC","enabled":1,"count":3}',
                      '{"code":"wrong","enabled":true,"count":3}'):
            with self.subTest(reply=reply):
                self.assertFalse(all(check['passed'] for check in check_reply('json', turn, reply, {'code': 'ABC'})))


if __name__ == '__main__':
    unittest.main()
