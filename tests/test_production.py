import io
import json
import os
import unittest
from email.message import Message
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from journey.client import Failure
from journey.hello import discover_workspace
from journey.production import BASE_URL, TOKEN_URL, ServiceAccount, knowledge_client, validate_selection


class ProductionTests(unittest.TestCase):
    def test_only_main_manual_journey3_is_allowed_in_production(self):
        environment = {'MOI_ENVIRONMENT': 'production', 'GITHUB_REF': 'refs/heads/main',
                       'GITHUB_EVENT_NAME': 'workflow_dispatch'}
        with patch.dict(os.environ, environment, clear=True):
            validate_selection(['journey-3'])
            with self.assertRaises(SystemExit):
                validate_selection(['journey-1', 'journey-3'])
            for key, value in [('GITHUB_REF', 'refs/heads/other'), ('GITHUB_EVENT_NAME', 'pull_request')]:
                with patch.dict(os.environ, {key: value}), self.assertRaises(SystemExit):
                    validate_selection(['journey-3'])
        with patch.dict(os.environ, {}, clear=True):
            validate_selection(['journey-1', 'journey-3'])

    def test_token_is_cached_and_renewed_before_expiration(self):
        account = ServiceAccount('uc-sa-test', 'synthetic-client-secret')
        def token_reply(token):
            return io.BytesIO(json.dumps({'access_token': token, 'expires_in': 300, 'token_type': 'Bearer'}).encode())
        account.opener.open = Mock(side_effect=[token_reply('first.test.signature'), token_reply('second.test.signature')])
        with patch('journey.production.time.monotonic', side_effect=[100, 200, 375]):
            self.assertEqual(account.bearer(60), 'first.test.signature')
            self.assertEqual(account.bearer(60), 'first.test.signature')
            self.assertEqual(account.bearer(60), 'second.test.signature')
        self.assertEqual(account.opener.open.call_count, 2)
        request = account.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, TOKEN_URL)
        self.assertEqual(parse_qs(request.data.decode())['grant_type'], ['client_credentials'])

    def test_production_client_sends_bearer_and_pinned_workspace_from_first_request(self):
        workspace = '00000000-0000-0000-0000-000000000003'
        settings = {'MOI_ENVIRONMENT': 'production', 'MOI_BASE_URL': BASE_URL,
                    'MOI_J3_WORKSPACE_ID': workspace, 'MOI_J3_CLIENT_ID': 'uc-sa-test',
                    'MOI_J3_CLIENT_SECRET': 'synthetic-client-secret', 'MOI_PAT': 'qa-pat'}
        with patch.dict(os.environ, settings, clear=True), \
                patch.object(ServiceAccount, 'bearer', return_value='one.two.three'):
            client = knowledge_client(60)
            def get(request, timeout):
                self.assertEqual(request.get_header('Authorization'), 'Bearer one.two.three')
                self.assertEqual(request.get_header('X-workspace-id'), workspace)
                self.assertIsNone(request.get_header('X-api-key'))
                if request.get_header('Accept') == 'text/event-stream':
                    frame = {'jsonrpc': '2.0', 'id': json.loads(request.data)['id'], 'result': {'kind': 'task'}}
                    response = io.BytesIO(('data: ' + json.dumps(frame) + '\n\n').encode())
                    response.status, response.headers = 200, Message()
                    response.headers['Content-Type'] = 'text/event-stream'
                    return response
                payload = {'code': 'OK', 'data': {
                    'workspaces': [{'id': workspace, 'name': 'journey-3', 'status': 'ACTIVE'}],
                }}
                response = io.BytesIO(json.dumps(payload).encode())
                response.status, response.headers = 200, Message()
                return response
            client.opener.open = Mock(side_effect=get)
            self.assertEqual(discover_workspace(client, 'journey-3'), '/workspaces/' + workspace)
            self.assertEqual(list(client.stream_rpc({'agent_id': 'example'}, {}))[0]['result'], {'kind': 'task'})
            with patch.dict(os.environ, {'MOI_BASE_URL': 'https://other.invalid'}), \
                    self.assertRaisesRegex(Failure, 'production_base_url_mismatch'):
                knowledge_client(60)
