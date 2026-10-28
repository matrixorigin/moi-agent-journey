import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from journey.evidence import Evidence
from journey.catalog import load_case
from journey.functional_case import FunctionalExecution
from journey.outcomes import assess
from journey.report import finish_case
from journey.run_case import Execution


class AgentAwareClient:
    def __init__(self, *, finish_before_cancel=False):
        self.deadline, self.requests = time.monotonic() + 30, 0
        self.conversations, self.tasks, self.states, self.archived = {}, {}, {}, []
        self.finish_before_cancel = finish_before_cancel

    def check_selector(self, selector, cid):
        if selector != self.conversations[cid]:
            raise AssertionError('wrong_agent_for_conversation')

    def product(self, method, path, body=None):
        self.requests += 1
        url, query = urlsplit(path), parse_qs(urlsplit(path).query)
        if method == 'POST' and url.path.endswith('/conversations'):
            self.conversations[body['id']] = {key: body[key] for key in ('agent_id', 'agent_workspace_id')}
            return {'id': body['id']}
        if method == 'PATCH' and '/conversations/' in url.path:
            cid = url.path.rsplit('/', 1)[-1]
            self.check_selector({key: query[key][0] for key in ('agent_id', 'agent_workspace_id')}, cid)
            self.archived.append(cid)
            return {'status': 'archived'}
        if '/agent-artifacts' in url.path:
            cid = query['conversation_id'][0]
            self.check_selector({key: query[key][0] for key in ('agent_id', 'agent_workspace_id')}, cid)
            return {'items': [], 'total': 0}
        if '/agent-runtime-tasks/' in url.path:
            task_id = url.path.split('/agent-runtime-tasks/')[1].split('/')[0]
            cid = self.tasks[task_id]
            self.check_selector({key: query[key][0] for key in ('agent_id', 'agent_workspace_id')}, cid)
            if url.path.endswith('/events'):
                events = [] if int(query['after_seq'][0]) else [
                    {'task_id': task_id, 'seq': 1, 'kind': 'thinking', 'payload': {}}]
                return {'items': events}
            return {'id': task_id, 'context_id': cid, 'trace_id': 'trace-' + task_id, 'event_cursor': 1}
        raise AssertionError('unexpected request')

    def stream_rpc(self, selector, params):
        self.requests += 1
        cid = params['message']['contextId']
        self.check_selector(selector, cid)
        task_id = 'task-' + str(len(self.tasks))
        self.tasks[task_id] = cid
        self.states[task_id] = 'completed' if self.finish_before_cancel else 'working'
        yield {'result': {'kind': 'task', 'id': task_id, 'contextId': cid,
                          'status': {'state': self.states[task_id]}}}
        if self.finish_before_cancel:
            return
        self.states[task_id] = 'completed'
        yield {'result': {'kind': 'status-update', 'taskId': task_id, 'contextId': cid,
                          'status': {'state': 'completed'}}}

    def rpc(self, selector, method, params):
        self.requests += 1
        cid = self.tasks[params['id']]
        self.check_selector(selector, cid)
        if method == 'tasks/cancel':
            self.states[params['id']] = 'canceled'
        return {'kind': 'task', 'id': params['id'], 'contextId': cid,
                'status': {'state': self.states[params['id']], 'message': {'role': 'agent',
                           'parts': [{'kind': 'text', 'text': 'done'}]}}}


class MultiAgentExecutionTests(unittest.TestCase):
    def test_each_agent_keeps_its_own_selector_for_tasks_events_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            client = AgentAwareClient()
            case = {'id': 'J2-09', 'title': 'multiple agents', 'workspace': 'journey-2', 'timeout_seconds': 30}
            execution = Execution(client, case, Evidence(Path(directory)), directory, 'model')
            execution.root = '/workspaces/ws'
            execution.selector = {'agent_id': 'momo', 'agent_workspace_id': 'system'}
            first = execution.conversation('builder')
            execution.send(first, 'builder:propose', 'first')
            second = execution.conversation('created', selector={'agent_id': 'created', 'agent_workspace_id': 'ws'})
            execution.send(second, 'created:run', 'second')
            execution.finalize()
            self.assertEqual(set(client.archived), {first, second})
            self.assertTrue(execution.report['capture_complete'])
            self.assertEqual(execution.report['harness_errors'], [])
            self.assertNotEqual(client.conversations[first], client.conversations[second])

    def test_requested_cancellation_is_reported_as_canceled_with_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            client = AgentAwareClient()
            case = load_case('J2-14')
            execution = Execution(client, case, Evidence(Path(directory) / 'J2-14'), directory, 'model')
            execution.root = '/workspaces/ws'
            execution.selector = {'agent_id': 'momo', 'agent_workspace_id': 'system'}
            cid = execution.conversation('cancel')
            entry, task = execution.send(cid, 'cancel:run', 'long task', cancel_after_admission=True)
            self.assertEqual(entry['cancel_request'], 'accepted')
            self.assertEqual(task['status']['state'], 'canceled')
            execution.report['sessions'].append({'id': 'cancel', 'cancellation_confirmed': True})
            execution.report['validation'] = {'checks': [
                {'session': 'cancel', 'rule': 'accepted', 'passed': True, 'detail': ''},
                {'session': 'cancel', 'rule': 'canceled', 'passed': True, 'detail': ''}]}
            execution.report['status'] = 'passed'
            execution.report['qa_version_start'] = {'version': 'v1', 'git_commit': 'sha1'}
            execution.report['qa_version_end'] = {'version': 'v1', 'git_commit': 'sha1'}
            execution.finalize()
            task_result, agent, delivery, quality = assess(case, execution.report, 'passed')
            self.assertEqual((task_result['state'], agent['state'], delivery['state'], quality['state']),
                             ('canceled', 'canceled', 'canceled', 'checked'))
            self.assertTrue(execution.report['capture_complete'])
            self.assertEqual(execution.report['harness_errors'], [])
            result = finish_case('J2-14', directory)
            self.assertEqual(result['functional']['state'], 'pass')
            self.assertEqual(result['task']['state'], 'canceled')
            self.assertEqual(result['test_status'], 'completed')

    def test_task_finishing_before_cancellation_is_blocked_not_a_false_product_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            client = AgentAwareClient(finish_before_cancel=True)
            case = load_case('J2-14')
            execution = FunctionalExecution(client, case, Evidence(Path(directory) / 'J2-14'), directory, 'model')
            selector = {'agent_id': 'momo', 'agent_workspace_id': 'system'}
            with patch('journey.functional_case.discover', return_value=('/workspaces/ws', selector)), \
                 patch('journey.functional_case.start_version'), patch('journey.functional_case.finish_version'):
                raw = execution.run()
            self.assertTrue(raw['blocked'])
            self.assertEqual(raw['tasks'][0]['cancel_request'], 'already_terminal')
            self.assertEqual(raw['harness_errors'], [])
            result = finish_case('J2-14', directory)
            self.assertEqual(result['functional']['state'], 'blocked')
            self.assertEqual(result['test_status'], 'completed')


if __name__ == '__main__':
    unittest.main()
