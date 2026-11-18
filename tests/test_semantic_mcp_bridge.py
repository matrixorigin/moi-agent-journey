import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock


path = Path(__file__).resolve().parents[1] / 'tools/journey3-semantic-mcp/bridge.py'
spec = importlib.util.spec_from_file_location('semantic_bridge', path)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

READ = 'newmoi_get_semantic_models_model_id_entries'
CREATE = 'newmoi_post_semantic_models_model_id_entries'
UPDATE = 'newmoi_put_semantic_models_model_id_entries_entry_id'


class SemanticMCPBridgeTests(unittest.TestCase):
    def setUp(self):
        self.upstream = Mock(return_value={'ok': True})
        self.service = bridge.Bridge('journey-3', {80001}, 80005, [], self.upstream)

    def call(self, name, model, **extra):
        return self.service.call({'name': name, 'arguments': {
            'workspace_id': 'journey-3', 'model_id': model, **extra,
        }})

    def test_uses_real_rest_contract_and_projects_data_for_mcp(self):
        result = self.call(READ, 80001, page_size=1, domain='test & literal')
        self.assertEqual(result['structuredContent'], {'ok': True})
        self.upstream.assert_called_once_with('GET',
            '/semantic-models/80001/entries?domain=test+%26+literal&page_size=1', None)

    def test_rejects_cross_workspace_and_all_writes_to_fixed_snapshot(self):
        for name, arguments in [
            (READ, {'workspace_id': 'production'}),
            (READ, {'model_id': 90002}),
            (CREATE, {'kind': 'business_rule', 'key': 'fixture', 'spec': {}}),
            (UPDATE, {'entry_id': 5, 'kind': 'business_rule', 'key': 'fixture', 'spec': {}}),
        ]:
            with self.subTest(name=name, arguments=arguments):
                values = {'workspace_id': 'journey-3', 'model_id': 80001, **arguments}
                self.assertTrue(self.service.call({'name': name, 'arguments': values})['isError'])
        self.upstream.assert_not_called()

    def test_updates_only_scratch_and_treats_id_as_integer(self):
        data = {'kind': 'business_rule', 'key': 'fixture', 'spec': {'summary': 'fixture'}}
        self.assertTrue(self.call(UPDATE, 80005, entry_id='../80001', **data)['isError'])
        self.assertEqual(self.call(UPDATE, 80005, entry_id=123, **data)['structuredContent'], {'ok': True})
        self.upstream.assert_called_once_with('PUT', '/semantic-models/80005/entries/123', data)
