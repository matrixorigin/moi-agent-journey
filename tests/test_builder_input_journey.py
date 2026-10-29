import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from journey.builder_flow import ProductCaseFailure
from journey.builder_input_journey import BuilderInputJourney
from journey.client import Failure
from journey.evidence import Evidence
from test_builder_flow import task


class BuilderInputTests(unittest.TestCase):
    def fixture(self, outcome):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        execution = SimpleNamespace(root='/workspaces/ws', model='model', tasks=[],
            case={'sessions': [{'id': 'builder_input'}]}, report={'run_id': '123', 'harness_errors': []},
            client=Mock(deadline=time.monotonic() + 30), evidence=Evidence(Path(directory.name)), checkpoint=Mock())
        journey = BuilderInputJourney(execution, [])
        def product(method, path, body=None):
            if method == 'GET' and path.endswith('/resources'):
                return {'skills': [], 'tools': []}
            if method == 'GET' and '/agents/' in path:
                raise Failure('http_error', 404)
            if method == 'POST' and path.endswith('/cancel'):
                return {'agent_id': journey.agent_id, 'candidate_version': 'v1', 'status': 'superseded'}
            if method == 'POST' and path.endswith('/candidates'):
                if isinstance(outcome, Exception):
                    raise outcome
                return task(journey.agent_id, 'v1', agent_id=journey.agent_id)['status']['message']['parts'][0]['data']['output']['output']
            raise AssertionError((method, path))
        execution.client.product.side_effect = product
        journey.publish_agent = Mock()
        return journey, execution

    def test_rejected_proposals_do_not_mutate_the_valid_request(self):
        journey, execution = self.fixture(Failure('http_error', 400))
        journey.prepare()
        posts = [call.args[2] for call in execution.client.product.call_args_list if call.args[0] == 'POST']
        self.assertEqual(len(posts), 2)
        self.assertNotIn('conversation_id', posts[0])
        self.assertEqual(posts[0]['mode'], 'create')
        self.assertEqual(posts[1]['conversation_id'], 'manual-123')
        self.assertEqual(posts[1]['mode'], 'invalid-mode')
        self.assertEqual([check['passed'] for check in journey.checks], [True] * 4)
        journey.publish_agent.assert_called_once()

    def test_unexpected_candidate_is_never_committed_and_is_canceled_by_its_identity(self):
        journey, execution = self.fixture('accepted')
        with self.assertRaisesRegex(ProductCaseFailure, 'invalid_candidate_accepted'):
            journey.prepare()
        journey.publish_agent.assert_not_called()
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'], 'completed')
        mutations = [call.args for call in execution.client.product.call_args_list if call.args[0] == 'POST']
        self.assertEqual(len(mutations), 2)
        self.assertTrue(mutations[-1][1].endswith('/' + journey.agent_id + '/candidate-versions/v1/cancel'))
        self.assertEqual(execution.report['harness_errors'], [])

    def test_unknown_admission_outcome_is_never_retried_or_reported_clean(self):
        journey, execution = self.fixture(Failure('transport_error'))
        with self.assertRaisesRegex(Failure, 'transport_error'):
            journey.prepare()
        journey.publish_agent.assert_not_called()
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.state['cleanup'], 'incomplete')
        self.assertEqual(execution.report['harness_errors'][0]['reason'], 'candidate_proposal_outcome_unconfirmed')
        self.assertEqual(sum(call.args[0] == 'POST' for call in execution.client.product.call_args_list), 1)

    def test_unexpected_foreign_candidate_is_not_canceled(self):
        journey, execution = self.fixture('accepted')
        product = execution.client.product.side_effect
        def foreign(method, path, body=None):
            result = product(method, path, body)
            if method == 'POST' and path.endswith('/candidates'):
                result['agent_id'] = 'foreign-agent'
            return result
        execution.client.product.side_effect = foreign
        with self.assertRaisesRegex(Failure, 'unexpected_candidate_identity'):
            journey.prepare()
        journey.cleanup(dependencies_confirmed=True)
        self.assertEqual(journey.builder.seen, {})
        self.assertEqual(journey.state['cleanup'], 'incomplete')
        self.assertFalse(any(call.args[0] == 'POST' and call.args[1].endswith('/cancel')
                             for call in execution.client.product.call_args_list))
