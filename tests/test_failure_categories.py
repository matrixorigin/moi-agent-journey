import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from journey.benchmark_run_report import details_for_case
from journey.catalog import load_case
from journey.client import Failure
from journey.evidence import Evidence
from journey.failure_categories import error_category, failure_counts
from journey.parallel_batch import case_outcome
from journey.run_case import Execution, agent_stream


class FailureCategoryTests(unittest.TestCase):
    def test_dependency_evaluator_and_harness_errors_remain_distinct(self):
        examples = [
            ({'stage': 'session:qa', 'reason': 'http_error', 'http_status': 503}, 'qa_service'),
            ({'stage': 'semantic_judge', 'reason': 'http_error', 'http_status': 503}, 'evaluation_service'),
            ({'stage': 'semantic_judge', 'reason': 'agent_runtime_admission_error'}, 'evaluation_service'),
            ({'stage': 'session:qa', 'reason': 'agent_runtime_admission_error'}, 'qa_runtime'),
            ({'stage': 'semantic_judge', 'reason': 'invalid_model_verdict'}, 'evaluator'),
            ({'stage': 'session:qa', 'reason': 'stream_closed_before_terminal'}, 'transport'),
            ({'stage': 'configuration', 'reason': 'KeyError'}, 'harness'),
        ]
        for error, expected in examples:
            with self.subTest(error=error):
                self.assertEqual(error_category(error), expected)
        result = {'harness_errors': [examples[0][0],
            {'stage': 'batch:execute', 'reason': 'process_failed'}]}
        self.assertEqual(failure_counts(result), {'qa_service': 1})
        self.assertEqual(failure_counts({'harness_errors': [
            {'stage': 'batch:execute', 'reason': 'process_timeout'}]}), {'harness': 1})

    def test_private_progress_prints_only_categories_and_stage_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = {'failure_categories': {'evaluation_service': 1}}
            self.assertEqual(case_outcome(root, 'J3-01', ['execute', 'report'], row), 'evaluation service error')
            row['failure_categories'] = {'PRIVATE_BUSINESS': 1}
            self.assertEqual(case_outcome(root, 'J3-01', ['execute'], row), 'report unavailable')

    def test_completed_answer_with_unavailable_grader_is_explained_in_html_details(self):
        report = {'tasks': [{'task_id': 'task', 'conversation_id': 'session', 'trace_id': 'trace'}],
            'rag': {'answers': [{'text': 'real answer'}]},
            'harness_errors': [{'stage': 'semantic_judge', 'reason': 'http_error', 'http_status': 503}]}
        row = {'case_id': 'J3-01', 'task_state': 'completed', 'benchmark_state': 'eligible',
            'duration_seconds': 10, 'model_round_count': 1, 'quality_score': None,
            'session_id': 'session', 'trace_id': 'trace'}
        details = details_for_case(row, report)
        self.assertEqual(len(details['errors']), 1)
        self.assertIn('评分服务', details['errors'][0][-1])
        self.assertIn('503', details['errors'][0][-1])
        self.assertFalse(details['scores'])

    def test_admission_timeout_preserves_reported_identity_without_inventing_a_task(self):
        payload = {'code': -32007, 'data': {'reason': 'AGENT_A2A_ADMISSION_TIMEOUT',
            'contextId': 'context', 'taskId': 'reported-task'}}
        def rejected():
            raise Failure('rpc_error', details=payload, trace_headers={'X-Trace-Id': 'request-trace'})
            yield
        execution = SimpleNamespace(tasks=[], pending_conversation='context')
        with self.assertRaises(Failure) as raised:
            list(agent_stream(rejected(), execution, 'context', 'qa:question'))
        self.assertEqual(raised.exception.code, 'agent_runtime_admission_error')
        self.assertEqual(execution.pending_rejection['reported_task_id'], 'reported-task')
        self.assertEqual(execution.tasks, [])
        self.assertEqual(raised.exception.trace_headers['X-Trace-Id'], 'request-trace')
        execution.pending_conversation = 'different-context'
        with self.assertRaises(Failure) as raised:
            list(agent_stream(rejected(), execution, 'context', 'qa:question'))
        self.assertEqual(raised.exception.code, 'rpc_error')


class InterruptedStreamTests(unittest.TestCase):
    def execution(self, root, states):
        client = Mock(requests=0, deadline=time.monotonic() + 60)
        def task(state):
            return {'kind': 'task', 'id': 'same-task', 'contextId': 'same-context', 'status': {'state': state}}
        client.stream_rpc.return_value = (item for item in [{'result': task('working')}])
        client.rpc.side_effect = [task(state) for state in states]
        execution = Execution(client, load_case('J3-01'), Evidence(root), root, 'model')
        execution.root = '/workspaces/test'
        execution.conversations.append({'id': 'same-context', 'selector': {'agent_id': 'agent',
                                                                                'agent_workspace_id': 'test'}})
        execution.capture = Mock()
        return execution

    def test_closed_stream_reconciles_original_task_once_and_keeps_the_incident(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.run_case.time.sleep'):
            execution = self.execution(Path(tmp), ['working', 'working', 'completed'])
            entry, task = execution.send('same-context', 'qa:question', 'question')
            self.assertEqual(task['status']['state'], 'completed')
            self.assertEqual(entry['task_id'], 'same-task')
            self.assertEqual(len(execution.tasks), 1)
            execution.client.stream_rpc.assert_called_once()
            for call in execution.client.rpc.call_args_list:
                self.assertEqual(call.args[1:], ('tasks/get', {'id': 'same-task'}))
            incident = execution.report['stream_interruptions'][0]
            self.assertEqual(incident['observed_task_state'], 'working')
            self.assertEqual(incident['reconciled_state'], 'completed')
            self.assertEqual(failure_counts(execution.report), {'transport': 1})
            execution.capture.assert_called_with(entry, final=True)

    def test_terminal_stream_with_lagging_task_snapshot_is_not_a_stream_interruption(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.run_case.time.sleep'):
            execution = self.execution(Path(tmp), ['working', 'completed'])
            execution.client.stream_rpc.return_value = (item for item in [{'result': {
                'kind':'task', 'id':'same-task', 'contextId':'same-context', 'status':{'state':'completed'}}}])
            _, task = execution.send('same-context', 'qa:question', 'question')
            self.assertEqual(task['status']['state'], 'completed')
            self.assertFalse(execution.report.get('stream_interruptions'))

    def test_terminal_failure_is_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.run_case.time.sleep'):
            execution = self.execution(Path(tmp), ['working', 'failed'])
            with self.assertRaises(Failure) as raised:
                execution.send('same-context', 'qa:question', 'question')
            self.assertEqual(raised.exception.code, 'agent_failed')
            execution.client.stream_rpc.assert_called_once()
            self.assertEqual(execution.client.rpc.call_count, 2)
            self.assertEqual(execution.report['stream_interruptions'][0]['reconciled_state'], 'failed')

    def test_read_failure_is_retained_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp, patch('journey.run_case.time.sleep'):
            execution = self.execution(Path(tmp), ['working'])
            first = execution.client.rpc.side_effect
            execution.client.rpc.side_effect = [next(first), Failure('http_error', 503)]
            with self.assertRaises(Failure) as raised:
                execution.send('same-context', 'qa:question', 'question')
            self.assertEqual(raised.exception.http_status, 503)
            execution.client.stream_rpc.assert_called_once()
            self.assertNotIn('reconciled_state', execution.report['stream_interruptions'][0])

    def test_original_deadline_bounds_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            execution = self.execution(Path(tmp), ['working'])
            execution.client.deadline = time.monotonic() - 1
            with self.assertRaises(Failure) as raised:
                execution.send('same-context', 'qa:question', 'question')
            self.assertEqual(raised.exception.code, 'deadline_exceeded')
            execution.client.stream_rpc.assert_called_once()
            self.assertEqual(execution.client.rpc.call_count, 1)


if __name__ == '__main__':
    unittest.main()
