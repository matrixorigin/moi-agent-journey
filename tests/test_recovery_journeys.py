import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.artifact_knowledge import ArtifactKnowledgeFixture
from journey.builder_flow import ProductCaseFailure
from journey.builder_recovery import BuilderRecoveryJourney
from journey.client import Failure
from journey.embedded_skill import EmbeddedSkillJourney
from journey.evidence import Evidence
from journey.knowledge_fixture import KnowledgeFixtureUnavailable
from journey.task_failure import expected_model_failure
from test_knowledge_fixture import FixtureClient


class FailureRecoveryTests(unittest.TestCase):
    def execution(self, *, state='failed', error='server_error', trace='trace', phase='live:failed'):
        execution = SimpleNamespace(tasks=[], report={'run_id': 'unique'}, checkpoint=Mock())
        def send(cid, sent_phase, prompt, **kwargs):
            self.assertEqual(kwargs, {'model': 'journey-missing-unique'})
            execution.tasks.append({'conversation_id': cid, 'phase': phase, 'state': state, 'task_id': 'task',
                'trace_id': trace, 'runtime_error': {'provider': 'astra', 'error_code': error}})
            if state == 'failed':
                raise Failure('agent_failed')
        execution.send = Mock(side_effect=send)
        return execution

    def test_only_real_failed_task_with_trace_counts_as_injected_failure(self):
        execution = self.execution()
        checks = []
        expected_model_failure(execution, 'cid', 'live:failed', 'hello', 'live', checks)
        self.assertTrue(checks[0]['passed'])
        self.assertEqual(execution.report['expected_failures'][0]['task_id'], 'task')
        execution.send.assert_called_once()

    def test_tool_error_success_unrelated_error_missing_trace_and_wrong_turn_do_not_pass(self):
        for defect in ({'state': 'completed'}, {'error': 'tool_error'}, {'trace': None}, {'phase': 'other'}):
            with self.subTest(defect=defect), self.assertRaises(ProductCaseFailure):
                expected_model_failure(self.execution(**defect), 'cid', 'live:failed', 'hello', 'live', [])

    def test_admission_error_is_not_misreported_as_a_failed_task(self):
        execution = self.execution()
        execution.send.side_effect = Failure('agent_runtime_admission_error')
        with self.assertRaisesRegex(Failure, 'agent_runtime_admission_error'):
            expected_model_failure(execution, 'cid', 'live:failed', 'hello', 'live', [])
        self.assertEqual(execution.tasks, [])


class CandidateConfirmationTests(unittest.TestCase):
    def test_conflicts_are_allowed_but_server_errors_and_distinct_versions_fail(self):
        candidate = {'source_digest': 'digest', 'load_version': '1.0.0'}
        success = {'agent_id': 'agent', 'workspace_id': 'ws', **candidate}
        for response, passed in [((409, None, Failure('http_error', 409)), True),
                                  ((200, success, None), True),
                                  ((500, None, Failure('http_error', 500)), False),
                                  ((200, {**success, 'load_version': '2.0.0'}, None), False)]:
            with self.subTest(response=response[0]):
                journey = BuilderRecoveryJourney.__new__(BuilderRecoveryJourney)
                journey.agent_id, journey.workspace, journey.session_id = 'agent', 'ws', 's'
                journey.execution = SimpleNamespace(root='/workspaces/ws', client=Mock(), report={}, checkpoint=Mock())
                journey.builder = SimpleNamespace(owned_published=set())
                journey.checks = []
                with patch('journey.builder_recovery.concurrent_products', return_value=[(200, success, None), response]):
                    if passed:
                        self.assertEqual(journey.concurrent_commit('/commit', {}, candidate), success)
                    else:
                        with self.assertRaises(ProductCaseFailure):
                            journey.concurrent_commit('/commit', {}, candidate)
                self.assertEqual(journey.checks[0]['passed'], passed)
                self.assertEqual(journey.builder.owned_published, {'agent'})

    def test_embedded_skill_inventory_never_claims_another_agents_resource(self):
        journey = EmbeddedSkillJourney.__new__(EmbeddedSkillJourney)
        journey.name, journey.workspace, journey.agent_id = 'logical-skill', 'ws', 'our-agent'
        item = {'id': 'skill', 'workspace_id': 'ws', 'metadata': {'agent_id': 'other-agent',
            'agent_package_custom_skill': True, 'package_skill_name': 'logical-skill'}}
        journey.request = Mock(return_value={'items': [item], 'total': 1})
        with self.assertRaisesRegex(Failure, 'embedded_skill_owner_mismatch'):
            journey.owned_skills()


class ArtifactClient(FixtureClient):
    def __init__(self, defect=None):
        super().__init__()
        self.kind, self.defect, self.appended = 'document', defect, False
        self.bodies = []

    def product(self, method, path, body=None, *, expected_code=0):
        self.bodies.append((method, path, body))
        if path.endswith('/agent-artifacts/resolve'):
            return {'items': [{'artifact_id': 'foreign' if self.defect == 'foreign' else 'artifact',
                'version': 1, 'file_id': 'our-file', 'volume_id': 7}], 'failed': []}
        if path.endswith('/create-empty'):
            self.model = {'id': 42, 'name': body['name'], 'tables': [], 'files': {'file_ids': []}}
            return {'model': self.model}
        if method == 'POST' and path.endswith('/sources'):
            if self.defect == 'rejected_append':
                raise Failure('http_error', 400)
            self.appended = True
            self.model['files']['file_ids'] = ['our-file']
            if self.defect == 'lost_append_response':
                raise Failure('transport_error')
            return {'added_sources': [{'row_id': 'source'}]}
        if '/sources?' in path and not self.appended:
            return {'items': [], 'total': 0}
        return super().product(method, path, body, expected_code=expected_code)


class ArtifactKnowledgeTests(unittest.TestCase):
    def fixture(self, defect=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        client = ArtifactClient(defect)
        execution = SimpleNamespace(root='/workspaces/ws', client=client, report={'run_id': 'unique', 'harness_errors': []},
                                    checkpoint=Mock(), evidence=Evidence(temp.name))
        fixture = ArtifactKnowledgeFixture.__new__(ArtifactKnowledgeFixture)
        fixture.path, fixture.kind, fixture.marker = Path(temp.name)/'generated.pdf', 'document', 'J2-ORION-6B2F5C193D'
        reader = patch('pypdf.PdfReader', return_value=SimpleNamespace(pages=[SimpleNamespace(
            extract_text=lambda: fixture.marker)]))
        reader.start()
        self.addCleanup(reader.stop)
        fixture.artifact = {'artifact_id': 'artifact', 'version': 1}
        fixture.register(execution, 'generated')
        fixture.state['conversation_id'] = 'cid'
        cleanup = patch('journey.artifact_knowledge.cleanup_artifacts')
        fixture.cleanup_outputs = cleanup.start()
        self.addCleanup(cleanup.stop)
        return fixture, client, execution

    def test_actual_volume_file_is_used_without_reuploading_and_cleanup_is_complete(self):
        fixture, client, execution = self.fixture()
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        append = [body for method, path, body in client.bodies if method == 'POST' and path.endswith('/sources')]
        self.assertEqual(append, [{'sources': [], 'source_selections': [{'kind': 'volume_files', 'volume_id': 7,
                                  'all_selected': False, 'selected_file_ids': ['our-file']}]}])
        self.assertFalse(any(method == 'UPLOAD' for method, _ in client.calls))
        fixture.cleanup()
        self.assertTrue(client.deleted)
        fixture.cleanup_outputs.assert_called_once_with(fixture, 'cid')
        self.assertEqual(execution.report['harness_errors'], [])

    def test_wrong_artifact_resolution_never_creates_a_knowledge_base(self):
        fixture, client, _ = self.fixture('foreign')
        with self.assertRaises(KnowledgeFixtureUnavailable):
            fixture.setup()
        self.assertIsNone(client.model)
        fixture.cleanup()
        self.assertEqual(fixture.state['cleanup'], 'not_created')

    def test_rejected_and_uncertain_append_cleanup_only_confirmed_owned_sources(self):
        for defect in ('rejected_append', 'lost_append_response'):
            with self.subTest(defect=defect):
                fixture, client, execution = self.fixture(defect)
                with self.assertRaises(KnowledgeFixtureUnavailable):
                    fixture.setup()
                fixture.cleanup()
                self.assertTrue(client.deleted)
                self.assertEqual(execution.report['harness_errors'], [])

    def test_foreign_file_in_owned_kb_blocks_cleanup(self):
        fixture, client, execution = self.fixture()
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        client.model['files']['file_ids'].append('foreign-file')
        fixture.cleanup()
        self.assertFalse(client.deleted)
        fixture.cleanup_outputs.assert_not_called()
        self.assertEqual(fixture.state['cleanup'], 'incomplete')

    def test_artifact_cleanup_failure_is_reported_and_not_marked_deleted(self):
        fixture, client, execution = self.fixture()
        with patch('journey.knowledge_fixture.time.sleep'):
            fixture.setup()
        fixture.cleanup_outputs.side_effect = Failure('native_files_cleanup_incomplete')
        fixture.cleanup()
        self.assertTrue(client.deleted)
        self.assertEqual(fixture.state['artifacts_cleanup'], 'incomplete')
        self.assertEqual(execution.report['harness_errors'][0]['stage'], 'cleanup_artifact_knowledge')


class GeneratedKnowledgeCleanupOrderTests(unittest.TestCase):
    def test_referenced_file_is_recorded_and_never_force_deleted(self):
        from journey.native_skill_cleanup import delete_owned_file
        execution = SimpleNamespace(root='/workspaces/ws', evidence=Mock(), client=Mock())
        execution.client.product.return_value = {
            'file_id': 'file', 'source_session_id': 'cid', 'source_task_id': 'task', 'ref_count': 1}
        with self.assertRaises(Failure) as error:
            delete_owned_file(execution, 'file', 'cid', {'task'})
        self.assertEqual(error.exception.details['ref_count'], 1)
        execution.evidence.append.assert_called_once()
        execution.client.request.assert_not_called()

    def test_unrelated_file_is_never_polled_or_deleted(self):
        from journey.native_skill_cleanup import delete_owned_file
        execution = SimpleNamespace(root='/workspaces/ws', evidence=Mock(), client=Mock(deadline=time.monotonic()+30))
        execution.client.product.return_value = {
            'file_id': 'file', 'source_session_id': 'other', 'source_task_id': 'task', 'ref_count': 1}
        with patch('journey.native_skill_cleanup.time.sleep') as sleep, self.assertRaises(Failure):
            delete_owned_file(execution, 'file', 'cid', {'task'})
        sleep.assert_not_called()
        execution.client.request.assert_not_called()

    def test_generated_knowledge_and_files_are_cleaned_before_conversation_archive(self):
        from journey.functional_case import FunctionalExecution
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        order = []
        client = Mock(deadline=time.monotonic() + 30, requests=0)
        client.product.side_effect = lambda *args: order.append('archive') or {'status': 'archived'}
        execution = FunctionalExecution(client, {'id': 'J2-128', 'title': 'cleanup', 'workspace': 'journey-2',
            'timeout_seconds': 300}, Evidence(directory.name), directory.name, 'model')
        execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        execution.conversations.append({'id': 'cid', 'selector': execution.selector})
        execution.tasks.append({'conversation_id': 'cid', 'state': 'completed'})
        execution.cleanup_tasks = Mock(side_effect=lambda: order.append('tasks'))
        execution.collect_artifacts = Mock(side_effect=lambda cid: order.append('inventory'))
        cleanup = Mock(side_effect=lambda **kwargs: order.append('knowledge-and-files'))
        execution.generated_knowledge = SimpleNamespace(state={'conversation_id': 'cid'}, cleanup=cleanup)
        execution.cleanup()
        self.assertEqual(order, ['tasks', 'inventory', 'knowledge-and-files', 'archive'])
        cleanup.assert_called_once_with(dependencies_confirmed=True)
        self.assertEqual(execution.report['harness_errors'], [])
