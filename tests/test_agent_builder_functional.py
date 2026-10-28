import tempfile
import time
import unittest
from pathlib import Path

from journey.agent_builder import check_candidate_cancel
from journey.client import Failure
from journey.evidence import Evidence
from journey.run_case import Execution


class BuilderClient:
    def __init__(self, *, accept_bad_digest=False):
        self.requests = 0
        self.deadline = time.monotonic() + 60
        self.status = 'valid'
        self.accept_bad_digest = accept_bad_digest
        self.mutations = []

    def task(self):
        output = {'render_type': 'agent_candidate', 'workspace_id': 'ws', 'agent_id': 'our-agent',
                  'candidate_version': 'v1', 'source_digest': 'sha256:' + 'a' * 64,
                  'status': self.status, 'mode': 'create',
                  'display': {'agent_id': 'our-agent', 'agent_name': 'our-agent'}}
        return {'kind': 'task', 'id': 'task', 'contextId': 'conversation',
                'status': {'state': 'completed', 'message': {'parts': [
                    {'kind': 'data', 'data': {'output': {'output': output}}}]}}}

    def rpc(self, selector, method, params):
        self.requests += 1
        return self.task()

    def product(self, method, path, body=None):
        self.requests += 1
        if method == 'GET':
            raise Failure('http_error', 404)
        if method != 'POST' or not path.endswith('/cancel'):
            raise AssertionError('unexpected mutation')
        self.mutations.append(body['source_digest'])
        if body['source_digest'] != 'sha256:' + 'a' * 64 and not self.accept_bad_digest:
            raise Failure('http_error', 409)
        self.status = 'superseded'
        return {'agent_id': 'our-agent', 'candidate_version': 'v1', 'status': self.status}


class BuilderFunctionalTests(unittest.TestCase):
    def run_case(self, accept_bad_digest=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        client = BuilderClient(accept_bad_digest=accept_bad_digest)
        case = {'id': 'J2-04', 'title': 'candidate', 'workspace': 'journey-2', 'timeout_seconds': 30}
        execution = Execution(client, case, Evidence(Path(tmp.name)), tmp.name, 'model')
        execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        checks = []
        verified = check_candidate_cancel(execution, client.task(), 'task', 'our-agent', checks, 'candidate')
        return execution, client, checks, verified

    def test_bad_digest_rejected_then_candidate_canceled_and_read_back(self):
        execution, client, checks, verified = self.run_case()
        self.assertTrue(verified)
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertEqual(len(checks), 8)
        self.assertEqual(len(client.mutations), 2)
        self.assertEqual(execution.report['agent_cleanup'], 'candidate_canceled')
        self.assertEqual(execution.report['harness_errors'], [])

    def test_accepting_bad_digest_is_a_product_failure_without_a_second_mutation(self):
        execution, client, checks, verified = self.run_case(accept_bad_digest=True)
        self.assertTrue(verified)
        self.assertFalse(next(check for check in checks if check['rule'] == '错误摘要被拒绝')['passed'])
        self.assertFalse(next(check for check in checks if check['rule'] == '错误摘要未改变候选')['passed'])
        self.assertEqual(len(client.mutations), 1)
        self.assertEqual(execution.report['agent_cleanup'], 'candidate_canceled')
        self.assertEqual(execution.report['harness_errors'], [])

    def test_preexisting_agent_is_neither_mutated_nor_archived(self):
        class ExistingClient(BuilderClient):
            def product(self, method, path, body=None):
                if method == 'GET':
                    return {'id': 'our-agent', 'name': 'our-agent', 'workspace_id': 'ws', 'status': 'active'}
                raise AssertionError('preexisting object must not be mutated')
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        client = ExistingClient()
        case = {'id': 'J2-04', 'title': 'candidate', 'workspace': 'journey-2', 'timeout_seconds': 30}
        execution = Execution(client, case, Evidence(Path(tmp.name)), tmp.name, 'model')
        execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        checks = []
        self.assertFalse(check_candidate_cancel(execution, client.task(), 'task', 'our-agent', checks, 'candidate'))
        self.assertFalse(next(check for check in checks if check['rule'] == '确认前无已发布智能体')['passed'])
        self.assertEqual(client.mutations, [])
        self.assertEqual(execution.report['agent_cleanup'], 'incomplete')
        self.assertTrue(any('cleanup_agent_ownership_unverified' in item.get('reason', '')
                            for item in execution.report['harness_errors']))


if __name__ == '__main__':
    unittest.main()
