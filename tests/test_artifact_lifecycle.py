import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from journey.artifact_lifecycle import check_artifact_delete
from journey.builder_flow import ProductCaseFailure
from journey.client import Failure
from journey.evidence import Evidence


class ArtifactLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inventory = self.root / 'artifacts' / 'conversation' / 'inventory.json'
        self.inventory.parent.mkdir(parents=True)
        self.artifact = {'artifact_id': 'artifact-1', 'version': 1, 'workspace_id': 'workspace',
                         'agent_id': 'agent', 'agent_workspace_id': 'system', 'conversation_id': 'conversation',
                         'task_id': 'task', 'file_name': 'result.txt'}
        self.ref = {'artifact_id': 'artifact-1', 'version': 1}
        self.inventory.write_text(json.dumps([self.artifact]))
        self.calls = []
        self.still_present = False
        self.wrong_result = False
        self.execution = SimpleNamespace(root='/workspaces/workspace', evidence=Evidence(self.root), report={},
            checkpoint=lambda: None, conversation_selector=lambda _: {'agent_id': 'agent', 'agent_workspace_id': 'system'},
            client=SimpleNamespace(product=self.product))

    def product(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.endswith('/batch-delete'):
            return {'succeeded': [] if self.wrong_result else [self.ref], 'failed': []}
        if path.endswith('/resolve'):
            if self.still_present:
                return {'items': [self.ref], 'failed': []}
            return {'items': [], 'failed': [{**self.ref, 'reason': 'not_found'}]}
        if '/versions/' in path:
            if self.still_present:
                return dict(self.artifact)
            raise Failure('http_error', 404)
        if '/agent-artifacts?' in path:
            items = [self.artifact] if self.still_present else []
            return {'items': items, 'total': len(items)}
        raise AssertionError((method, path))

    def test_delete_verifies_result_detail_resolve_and_list(self):
        for still_present, wrong_result in ((False, False), (True, False), (False, True)):
            with self.subTest(still_present=still_present, wrong_result=wrong_result):
                self.still_present, self.wrong_result = still_present, wrong_result
                checks = []
                check_artifact_delete(self.execution, 'conversation', 'task', 'result.txt', checks, 'artifact')
                self.assertEqual(len(checks), 5)
                self.assertEqual(all(item['passed'] for item in checks), not still_present and not wrong_result)
                if still_present:
                    self.assertIn('artifact_detail_status:present', [item['detail'] for item in checks])
                    self.assertIn('artifact_resolve_mismatch', [item['detail'] for item in checks])
                    self.assertIn('deleted_artifact_still_listed', [item['detail'] for item in checks])

    def test_never_deletes_when_identity_is_not_this_task(self):
        for key, value in [('task_id', 'other'), ('conversation_id', 'other'), ('workspace_id', 'other'),
                           ('agent_id', 'other'), ('version', 0)]:
            with self.subTest(key=key):
                item = dict(self.artifact, **{key: value})
                self.inventory.write_text(json.dumps([item]))
                self.calls.clear()
                checks = []
                with self.assertRaises(ProductCaseFailure):
                    check_artifact_delete(self.execution, 'conversation', 'task', 'result.txt', checks, 'artifact')
                self.assertEqual(self.calls, [])
                self.assertFalse(checks[0]['passed'])


if __name__ == '__main__':
    unittest.main()
