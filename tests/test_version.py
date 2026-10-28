import unittest

from journey.client import Failure
from journey.version import read_moi_version


class VersionTests(unittest.TestCase):
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
