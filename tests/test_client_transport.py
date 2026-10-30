import http.client
import io
import json
from email.message import Message
import socket
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from journey.client import Client, Failure, transport_details


class ClientTransportTests(unittest.TestCase):
    def test_task_and_event_collections_exceed_ordinary_json_budget_without_truncation(self):
        content = 'journal-data-' + 'x' * 33_000_000
        client = Client('https://example.invalid', 'synthetic-pat')
        def open_response(request, timeout):
            value = {'code': 0, 'data': {'content': content}}
            if request.data:
                body = json.loads(request.data)
                value = {'jsonrpc': '2.0', 'id': body['id'], 'result': {'content': content}}
            response = io.BytesIO(json.dumps(value).encode())
            response.status = 200; response.headers = Message()
            return response
        client.opener.open = Mock(side_effect=open_response)
        self.assertEqual(client.rpc({'agent_id': 'a'}, 'tasks/get', {'id': 'task'})['content'], content)
        self.assertEqual(client.product('GET', '/workspaces/ws/agent-runtime-tasks/task/events?after_seq=0')['content'], content)
        with self.assertRaises(Failure) as raised:
            client.product('GET', '/workspaces/ws/agents/agent')
        self.assertEqual(raised.exception.code, 'response_too_large')
        with patch('journey.client.TASK_EVIDENCE_RESPONSE_LIMIT', 100), self.assertRaises(Failure) as raised:
            client.rpc({'agent_id': 'a'}, 'tasks/get', {'id': 'task'})
        self.assertEqual(raised.exception.code, 'response_too_large')

    def test_large_artifact_frames_are_preserved_and_frame_budget_resets(self):
        client = Client('https://example.invalid', 'synthetic-pat')
        payload = 'large artifact ' + '界' * 1_000_000
        def open_stream(request, timeout):
            body = json.loads(request.data)
            frame = {'jsonrpc': '2.0', 'id': body['id'], 'result': {'artifact': payload}}
            response = io.BytesIO((('data: ' + json.dumps(frame, ensure_ascii=False) + '\n\n') * 2).encode())
            response.status = 200
            response.headers = Message(); response.headers['Content-Type'] = 'text/event-stream'
            return response
        client.opener.open = Mock(side_effect=open_stream)
        frames = list(client.stream_rpc({'agent_id': 'a'}, {}))
        self.assertEqual([frame['result']['artifact'] for frame in frames], [payload, payload])

    def test_stream_limit_bounds_single_lines_and_multiline_utf8_bytes(self):
        for data in ('data: ' + 'x' * 300 + '\n\n',
                     'data: ' + '界' * 70 + '\ndata: ' + '界' * 70 + '\n\n'):
            client = Client('https://example.invalid', 'synthetic-pat')
            response = io.BytesIO(data.encode())
            response.status = 200
            response.headers = Message(); response.headers['Content-Type'] = 'text/event-stream'
            client.opener.open = Mock(return_value=response)
            with patch('journey.client.JSON_RESPONSE_LIMIT', 256), self.assertRaises(Failure) as raised:
                list(client.stream_rpc({'agent_id': 'a'}, {}))
            self.assertEqual(raised.exception.code, 'stream_frame_too_large')

    def test_upload_response_code_is_endpoint_specific(self):
        client = Client('https://example.invalid', 'synthetic-pat')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.zip'
            path.write_bytes(b'synthetic')
            for expected in ('OK', 0):
                for actual in ('OK', 0, 1):
                    client.request = Mock(return_value={'code': actual, 'data': {'id': 'fixture'}})
                    if actual == expected:
                        self.assertEqual(client.upload_multipart('/upload', path, expected_code=expected), {'id': 'fixture'})
                    else:
                        with self.assertRaises(Failure):
                            client.upload_multipart('/upload', path, expected_code=expected)

    def test_network_failures_are_classified_without_remote_error_text(self):
        for error, category in (
            (urllib.error.URLError(socket.gaierror(-2, 'private-host.example')), 'dns'),
            (urllib.error.URLError(ssl.SSLCertVerificationError(1, 'private-host.example')), 'tls_certificate'),
            (urllib.error.URLError(ConnectionRefusedError(111, 'private-host.example')), 'connection_refused'),
            (urllib.error.URLError('private-host.example'), 'network'),
            (http.client.RemoteDisconnected('private-host.example'), 'connection_closed'),
            (http.client.BadStatusLine('private-host.example'), 'http_protocol'),
        ):
            with self.subTest(category=category):
                details = transport_details(error)
                self.assertEqual(details['category'], category)
                self.assertNotIn('private-host', str(details))

    def test_stream_error_preserves_only_trace_headers(self):
        observations = []
        client = Client('https://example.invalid', 'synthetic-pat', observer=observations.append)
        def open_stream(request, timeout):
            body = json.loads(request.data)
            frame = {'jsonrpc': '2.0', 'id': body['id'], 'error': {'code': -32603}}
            response = io.BytesIO(('data: ' + json.dumps(frame) + '\n\n').encode())
            response.status = 200
            response.headers = Message()
            for name, value in {'Content-Type': 'text/event-stream', 'X-Trace-Id': 'request-trace',
                                'X-Request-Id': 'request-id', 'Set-Cookie': 'synthetic-cookie'}.items():
                response.headers[name] = value
            return response
        client.opener.open = Mock(side_effect=open_stream)
        with self.assertRaises(Failure) as raised:
            list(client.stream_rpc({'agent_id': 'a'}, {}))
        self.assertEqual(raised.exception.code, 'rpc_error')
        self.assertEqual(raised.exception.trace_headers, {'X-Trace-Id': 'request-trace', 'X-Request-Id': 'request-id'})
        self.assertTrue(all(item['headers'] == raised.exception.trace_headers for item in observations))
        self.assertNotIn('synthetic-cookie', str(observations))

    def test_http_and_stream_paths_preserve_the_same_safe_category(self):
        client = Client('https://example.invalid', 'synthetic-pat')
        client.opener.open = Mock(side_effect=urllib.error.URLError(socket.gaierror(-2, 'private-host.example')))
        for call in (lambda: client.request('GET', '/endpoint'),
                     lambda: list(client.stream_rpc({'agent_id': 'a'}, {}))):
            with self.subTest(call=call), self.assertRaises(Failure) as raised:
                call()
            self.assertEqual(raised.exception.code, 'transport_error')
            self.assertEqual(raised.exception.details, {'category': 'dns', 'exception': 'gaierror', 'errno': -2})


if __name__ == '__main__':
    unittest.main()
