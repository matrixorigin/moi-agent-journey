import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from journey.client import Failure
from journey.version import finish_version, read_moi_version


class VersionTests(unittest.TestCase):
    def test_end_version_network_error_keeps_diagnostics_and_restores_deadline(self):
        failure = Failure('transport_error', details={'category': 'connection_reset', 'exception': 'ConnectionResetError'})
        client = SimpleNamespace(deadline=123, product=Mock(side_effect=failure))
        execution = SimpleNamespace(client=client, report={'qa_version_start': {'version': 'v1'}, 'harness_errors': []})
        finish_version(execution)
        self.assertEqual(client.deadline, 123)
        client.product.assert_called_once()
        self.assertEqual(execution.report['harness_errors'][0]['reason'], 'transport_error')
        self.assertEqual(execution.report['harness_errors'][0]['details']['category'], 'connection_reset')

    def test_requires_one_identifiable_moi_backend(self):
        class Client:
            def __init__(self, items):
                self.items = items
            def product(self, method, path, expected_code):
                self.asserted = (method, path, expected_code)
                return {'backend_components': self.items}
        valid = {'component': 'moi-backend', 'version': 'v1', 'git_commit': 'abc'}
        client = Client([valid, {'component': 'other'}])
        self.assertEqual(read_moi_version(client), {'version': 'v1', 'git_commit': 'abc'})
        self.assertEqual(client.asserted, ('GET', '/system/versions', 'OK'))
        with self.assertRaises(Failure):
            read_moi_version(Client([valid, valid]))
        with self.assertRaises(Failure):
            read_moi_version(Client([{'component': 'moi-backend', 'version': 'v1'}]))


if __name__ == '__main__':
    unittest.main()
