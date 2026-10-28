import http.client
import socket
import ssl
import unittest
import urllib.error
from unittest.mock import Mock

from journey.client import Client, Failure, transport_details


class ClientTransportTests(unittest.TestCase):
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
