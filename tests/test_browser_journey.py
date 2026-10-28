import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from journey.agent_builder import candidate_from_task
from journey.browser_case import cleanup, fetch_tasks
from journey.catalog import load_case, load_suite
from journey.client import Failure
from journey.diagnostics import analyze_events
from journey.evidence import Evidence
from journey.report import _qa_versions, _valid_case_result, aggregate, finish_case
from journey.run_case import Execution


class FakeClient:
    def __init__(self):
        self.requests = 0
        self.deadline = 10**12
        self.queries = []

    def product(self, method, path, body=None):
        from urllib.parse import parse_qs, urlsplit
        self.requests += 1
        self.queries.append((method, path, body))
        q = parse_qs(urlsplit(path).query)
        if '/events?' in path:
            task_id = path.split('/agent-runtime-tasks/')[1].split('/')[0]
            return {'items': [] if q.get('after_seq') == ['1'] else
                    [{'task_id': task_id, 'seq': 1, 'kind': 'lifecycle', 'payload': {'type': 'started'}}]}
        if '/agent-runtime-tasks/' in path:
            task_id = path.split('/agent-runtime-tasks/')[1].split('?')[0]
            index = task_id[-1]
            return {'id': task_id, 'context_id': 'c' + index, 'trace_id': 'trace' + index,
                    'event_cursor': 1, 'created_at': '2026-01-01T00:00:00Z',
                    'updated_at': '2026-01-01T00:00:02Z'}
        if '/agent-runtime-tasks?' in path:
            phase = q['context_id'][0][-1]
            return {'items': [{'id': 'task' + phase, 'context_id': 'c' + phase,
                               'agent_id': q['agent_id'][0], 'agent_workspace_id': q['agent_workspace_id'][0],
                               'a2a_state': 'completed'}]}
        raise AssertionError(path)

    def rpc(self, selector, method, params):
        index = params['id'][-1]
        return {'kind': 'task', 'id': params['id'], 'contextId': 'c' + index,
                'status': {'state': 'completed', 'message': {'parts': []}}}


class BrowserJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.case = load_case('J2-01')

    def tearDown(self):
        self.tmp.cleanup()

    def write_result(self, status, *, tasks=2, verified=True, checks=True, blocked=False):
        directory = self.root / 'J2-01'
        directory.mkdir(parents=True)
        events = []
        entries = []
        for index, phase in enumerate(('create', 'verify')[:tasks], 1):
            entries.append({'phase': 'browser:' + phase, 'task_id': 't' + str(index), 'conversation_id': 'c' + str(index),
                            'trace_id': 'trace' + str(index), 'state': 'completed', 'event_count': 1,
                            'capture_complete': True})
            events.append({'task_id': 't' + str(index), 'kind': 'lifecycle', 'seq': 1,
                           'payload': {'type': 'started'}, 'phase': 'browser:' + phase})
        (directory / 'events.jsonl').write_text(''.join(json.dumps(item) + '\n' for item in events))
        result = {'case_id': 'J2-01', 'workspace': 'journey-2', 'title': self.case['title'], 'status': status,
                  'tasks': entries, 'capture_complete': bool(entries), 'failures': [], 'harness_errors': [],
                  'blocked': blocked, 'sessions': [{'id': 'browser', 'created_agent': {'verified': verified}}]
                  if verified is not None else [], 'validation': {'checks': []},
                  'qa_version_start': {'version': 'v1', 'git_commit': 'abc'},
                  'qa_version_end': {'version': 'v1', 'git_commit': 'abc'}}
        if checks:
            result['validation']['checks'] = [{'session': 'browser', 'rule': 'candidate', 'passed': status == 'passed',
                                                'detail': '' if status == 'passed' else 'not_visible'}]
        if status == 'failed':
            result['failures'].append({'stage': 'candidate', 'reason': 'candidate_card_not_visible'})
        if status == 'error':
            result['harness_errors'].append({'stage': 'login', 'reason': 'login_or_browser_unavailable'})
        (directory / 'result.json').write_text(json.dumps(result))
        return directory

    def test_two_browser_requests_capture_separate_agents_and_traces(self):
        client = FakeClient()
        execution = Execution(client, self.case, Evidence(self.root / 'evidence'), self.root, self.case['model'])
        execution.root = '/workspaces/ws'
        execution.selector = {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        execution.agent_name, execution.workspace_id = 'test', 'ws'
        observations = [
            {'kind': 'task_request', 'phase': 'create', 'conversation_id': 'c1',
             'agent_id': 'momo', 'agent_workspace_id': 'system'},
            {'kind': 'task_request', 'phase': 'verify', 'conversation_id': 'c2',
             'agent_id': 'created', 'agent_workspace_id': 'ws'},
        ]
        fetch_tasks(execution, observations, 10**12)
        self.assertEqual([task['trace_id'] for task in execution.tasks], ['trace1', 'trace2'])
        self.assertTrue(all(task['capture_complete'] for task in execution.tasks))
        self.assertEqual([task['selector']['agent_id'] for task in execution.tasks], ['momo', 'created'])
        lines = (self.root / 'evidence' / 'events.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), 2)

    def test_candidate_cleanup_identity_must_match_the_exact_synthetic_name(self):
        data = {'agent_id': 'id', 'candidate_version': 'v1', 'source_digest': 'digest',
                'workspace_id': 'ws', 'mode': 'create', 'status': 'valid', 'render_type': 'agent_candidate',
                'display': {'agent_id': 'id', 'agent_name': 'our-test'}}
        task = {'status': {'message': {'parts': [{'data': {'output': {'output': data}}}]}}}
        self.assertEqual(candidate_from_task(task, 'our-test', 'ws')['agent_id'], 'id')
        self.assertIsNone(candidate_from_task(task, 'another-test', 'ws'))
        task['status']['message']['parts'].append(task['status']['message']['parts'][0])
        with self.assertRaises(Failure):
            candidate_from_task(task, 'our-test', 'ws')

    def test_cleanup_never_archives_an_agent_with_a_different_name(self):
        class CleanupClient(FakeClient):
            def product(self, method, path, body=None):
                self.queries.append((method, path, body))
                if method == 'GET':
                    return {'id': 'id', 'name': 'somebody-elses-agent', 'workspace_id': 'ws', 'status': 'active'}
                raise AssertionError('must not mutate an unrelated agent')
        client = CleanupClient()
        execution = Execution(client, self.case, Evidence(self.root / 'evidence'), self.root, self.case['model'])
        execution.root, execution.workspace_id, execution.agent_name = '/workspaces/ws', 'ws', 'our-test'
        execution.candidate = {'agent_id': 'id', 'candidate_version': 'v1', 'source_digest': 'digest'}
        execution.commit = None
        cleanup(execution)
        self.assertEqual(execution.report['agent_cleanup'], 'incomplete')
        self.assertEqual([method for method, _, _ in client.queries], ['GET'])
        self.assertIn('cleanup_agent_identity_mismatch', execution.report['harness_errors'][0]['reason'])

    def test_browser_cleanup_requires_matching_successful_commit(self):
        class CleanupClient(FakeClient):
            def product(self, method, path, body=None):
                self.queries.append((method, path, body))
                if method == 'GET':
                    return {'id': 'id', 'name': 'our-test', 'workspace_id': 'ws', 'status': 'active'}
                if method == 'PATCH':
                    return {'status': 'archived'}
                raise AssertionError('unexpected mutation')
        for proof in ('missing', 'mismatch', 'verified'):
            with self.subTest(proof=proof):
                client = CleanupClient()
                execution = Execution(client, self.case, Evidence(self.root / proof), self.root, self.case['model'])
                execution.root, execution.workspace_id, execution.agent_name = '/workspaces/ws', 'ws', 'our-test'
                execution.candidate = {'agent_id': 'id', 'candidate_version': 'v1', 'source_digest': 'digest'}
                execution.commit = {'agent_id': 'id', 'candidate_version': 'v1', 'source_digest': 'digest'}
                execution.published = ({'agent_id': 'id', 'candidate_version': 'v1', 'source_digest': 'digest',
                                        'workspace_id': 'ws', 'version': '1.0.0'} if proof != 'missing' else None)
                if proof == 'mismatch':
                    execution.published['source_digest'] = 'other'
                cleanup(execution)
                patches = [method for method, _, _ in client.queries if method == 'PATCH']
                self.assertEqual(patches, ['PATCH'] if proof == 'verified' else [])
                self.assertEqual(execution.report['agent_cleanup'], 'archived' if proof == 'verified' else 'incomplete')

    def test_capture_failure_in_one_turn_does_not_drop_the_other_turn(self):
        class CaptureClient(FakeClient):
            def product(self, method, path, body=None):
                if '/agent-runtime-tasks/task1?' in path:
                    raise Failure('first_capture_failed')
                return super().product(method, path, body)
        client = CaptureClient()
        execution = Execution(client, self.case, Evidence(self.root / 'evidence'), self.root, self.case['model'])
        execution.root = '/workspaces/ws'
        execution.selector = {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        execution.agent_name, execution.workspace_id = 'test', 'ws'
        fetch_tasks(execution, [
            {'kind': 'task_request', 'phase': 'create', 'conversation_id': 'c1',
             'agent_id': 'momo', 'agent_workspace_id': 'system'},
            {'kind': 'task_request', 'phase': 'verify', 'conversation_id': 'c2',
             'agent_id': 'created', 'agent_workspace_id': 'ws'},
        ], 10**12)
        self.assertEqual(len(execution.tasks), 2)
        self.assertFalse(execution.tasks[0]['capture_complete'])
        self.assertTrue(execution.tasks[1]['capture_complete'])
        self.assertEqual(execution.tasks[1]['trace_id'], 'trace2')
        self.assertEqual(execution.report['harness_errors'][0]['stage'], 'capture_browser_task')

    def test_cancel_failure_still_collects_available_events_and_keeps_active_conversation(self):
        class CancelClient(FakeClient):
            def rpc(self, selector, method, params):
                if method == 'tasks/cancel':
                    raise Failure('cancel_failed')
                return super().rpc(selector, method, params)
        client = CancelClient()
        execution = Execution(client, self.case, Evidence(self.root / 'evidence'), self.root, self.case['model'])
        execution.root = '/workspaces/ws'
        execution.agent_name, execution.workspace_id = 'test', 'ws'
        execution.candidate = execution.commit = None
        selector = {'agent_id': 'momo', 'agent_workspace_id': 'system'}
        execution.tasks.append({'task_id': 'task1', 'conversation_id': 'c1', 'phase': 'browser:create',
                                'state': 'working', 'selector': selector, 'event_cursor': 0,
                                'event_count': 0, 'event_kinds': {}, 'capture_complete': False})
        execution.conversations.append({'id': 'c1', 'selector': selector, 'cleanup': 'pending'})
        cleanup(execution)
        self.assertEqual(execution.tasks[0]['event_count'], 1)
        self.assertEqual(execution.tasks[0]['trace_id'], 'trace1')
        self.assertFalse(execution.tasks[0]['capture_complete'])
        self.assertEqual(execution.conversations[0]['cleanup'], 'incomplete')
        self.assertIn('cancel_task', [item['stage'] for item in execution.report['harness_errors']])
        self.assertFalse(any(method == 'PATCH' for method, _, _ in client.queries))

    def test_functional_pass_requires_readback_and_two_completed_turns(self):
        self.write_result('passed')
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['test_status'], 'completed')
        self.assertEqual(result['functional']['state'], 'pass')
        self.assertEqual(result['agent']['completed_turns'], 2)
        self.assertEqual(result['delivery']['state'], 'delivered')

    def test_missing_card_is_a_green_product_failure_with_trace(self):
        self.write_result('failed', tasks=1, verified=None)
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['test_status'], 'completed')
        self.assertEqual(result['functional']['state'], 'fail')
        self.assertEqual(result['agent']['state'], 'unknown')
        self.assertEqual(result['tasks'][0]['trace_id'], 'trace1')
        self.assertIn('candidate_card_not_visible', (self.root / 'J2-01' / 'case-summary.md').read_text())

    def test_pass_without_verified_agent_is_a_framework_error(self):
        self.write_result('passed', verified=False)
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['test_status'], 'error')
        self.assertEqual(result['functional']['state'], 'error')
        self.assertIn('verified_agent_missing', [item['reason'] for item in result['harness_errors']])

    def test_pass_without_both_captured_tasks_is_a_framework_error(self):
        self.write_result('passed', tasks=1)
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['functional']['state'], 'error')
        self.assertIn('functional_turns_not_fully_captured', [item['reason'] for item in result['harness_errors']])

    def test_pass_without_both_backend_version_observations_is_a_framework_error(self):
        directory = self.write_result('passed')
        raw = json.loads((directory / 'result.json').read_text())
        raw.pop('qa_version_end')
        (directory / 'result.json').write_text(json.dumps(raw))
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['functional']['state'], 'error')
        self.assertIn('qa_backend_version_missing', [item['reason'] for item in result['harness_errors']])

    def test_pass_with_changed_backend_version_is_a_framework_error(self):
        directory = self.write_result('passed')
        raw = json.loads((directory / 'result.json').read_text())
        raw['qa_version_end'] = {'version': 'v2', 'git_commit': 'def'}
        (directory / 'result.json').write_text(json.dumps(raw))
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['functional']['state'], 'error')
        self.assertIn('qa_version_verdict_inconsistent', [item['reason'] for item in result['harness_errors']])

    def test_finalized_pass_with_mismatched_versions_is_not_a_valid_artifact(self):
        self.write_result('passed')
        result = finish_case('J2-01', self.root)
        self.assertTrue(_valid_case_result(result))
        result['qa_version_end'] = {'version': 'v2', 'git_commit': 'def'}
        self.assertFalse(_valid_case_result(result))

    def test_login_blocked_and_missing_case_are_not_counted_as_pass(self):
        self.write_result('error', tasks=0, verified=None, checks=False, blocked=True)
        result = finish_case('J2-01', self.root)
        self.assertEqual(result['functional']['state'], 'blocked')
        with tempfile.TemporaryDirectory() as destination:
            report = aggregate(self.root, Path(destination) / 'bundle')
            suite = next(item for item in report['journeys'] if item['workspace'] == 'journey-2')
            expected_missing = len(load_suite('journey-2')['cases']) - 1
            self.assertEqual(suite['functional'], {'pass': 0, 'fail': 0, 'blocked': 1, 'error': expected_missing})
            self.assertEqual(suite['coverage_status'], 'incomplete')
            self.assertIn('当前结果不能作为发布门禁', (Path(destination) / 'bundle' / 'summary.md').read_text())
            (self.root / 'J2-01' / 'result.json').unlink()
            report = aggregate(self.root, Path(destination) / 'bundle2')
            suite = next(item for item in report['journeys'] if item['workspace'] == 'journey-2')
            self.assertEqual(suite['functional']['error'], expected_missing + 1)

    def test_ui_credentials_are_redacted_from_saved_reports(self):
        directory = self.write_result('error', tasks=0, verified=None, checks=False, blocked=True)
        result = json.loads((directory / 'result.json').read_text())
        result['harness_errors'][0]['reason'] = 'my-private-account my-private-password'
        (directory / 'result.json').write_text(json.dumps(result))
        with patch.dict('os.environ', {'MOI_UI_ACCOUNT': 'my-private-account',
                                      'MOI_UI_PASSWORD': 'my-private-password'}):
            finish_case('J2-01', self.root)
        text = (directory / 'result.json').read_text() + (directory / 'case-summary.md').read_text()
        self.assertNotIn('my-private-account', text)
        self.assertNotIn('my-private-password', text)

    def test_mixed_or_missing_backend_versions_cannot_be_reported_as_one_version(self):
        v1 = {'version': 'v1', 'git_commit': 'aaa'}
        v2 = {'version': 'v2', 'git_commit': 'bbb'}
        first = {'case_id': 'J2-01', 'qa_version_start': v1, 'qa_version_end': v1}
        second = {'case_id': 'J2-02', 'qa_version_start': v1, 'qa_version_end': v2}
        self.assertEqual(_qa_versions([first])['state'], 'consistent')
        self.assertEqual(_qa_versions([first, second])['state'], 'mixed')
        self.assertEqual(_qa_versions([first, {'case_id': 'J2-02'}])['state'], 'unknown')

    def test_browser_http_log_excludes_unrelated_agent_and_owner_data(self):
        evidence = Evidence(self.root)
        evidence.observe_transport({'method': 'GET', 'path': '/workspaces/ws/agents',
            'status': 200, 'seconds': 0.1, 'headers': {},
            'request': {'email': 'private-owner@example.com'},
            'response': {'instruction': 'another agents private instruction'}})
        logged = json.loads((self.root / 'http.jsonl').read_text())
        self.assertEqual(logged['status'], 200)
        self.assertNotIn('request', logged)
        self.assertNotIn('response', logged)

    def test_loaded_candidate_projection_does_not_create_a_false_tool_failure(self):
        path = self.root / 'events.jsonl'
        def verdict(status):
            event = {'task_id': 't1', 'seq': 1, 'kind': 'tool_result', 'payload': {
                'call_id': 'call1', 'tool_id': 'agent_builder', 'success': True,
                'output': {'output': {'render_type': 'agent_candidate', 'status': status, 'ok': False}}}}
            path.write_text(json.dumps(event) + '\n')
            return analyze_events(path, [{'task_id': 't1', 'event_count': 1, 'phase': 'browser:create'}])
        loaded = verdict('loaded')
        self.assertEqual(loaded['metrics']['succeeded'], 1)
        self.assertEqual(loaded['errors'], [])
        invalid = verdict('invalid')
        self.assertEqual(invalid['metrics']['failed'], 1)
        self.assertEqual(invalid['errors'][0]['errors'][0]['kind'], 'tool_output_failure')


if __name__ == '__main__':
    unittest.main()
