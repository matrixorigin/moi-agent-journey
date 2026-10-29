"""Reject malformed manual proposals, then publish and use a case-owned Agent."""
import copy

from .builder_flow import AgentBuilderFlow
from .client import Failure, error_record
from .resource_journey import ResourceJourney


class BuilderInputJourney(ResourceJourney):
    kind = 'agent'

    def __init__(self, execution, checks):
        self.execution, self.checks = execution, checks
        self.session_id = execution.case['sessions'][0]['id']
        self.workspace = execution.root.rsplit('/', 1)[-1]
        self.agent_id = 'j2-agent-' + execution.report['run_id'].replace('-', '')
        self.agent_pending, self.builder = False, None
        self.state = {'cleanup': 'pending'}
        execution.report['builder_input_journey'] = self.state
        self.previous_observer = execution.client.observer
        execution.client.observer = self.observe

    def prepare(self):
        if not self.absent(self.agent_path):
            raise Failure('resource_agent_id_exists')
        self.builder = AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id])
        instruction = 'Follow the user instructions exactly. For a request to echo a code, return only that code.'
        original = self.candidate_request([], instruction, self.request('GET', '/agent-builder/resources'))
        for defect in ('conversation_id', 'mode'):
            body = copy.deepcopy(original)
            if defect == 'conversation_id':
                body.pop(defect)
            else:
                body[defect] = 'invalid-mode'
            self.state['candidate_proposal_pending'] = True
            self.execution.checkpoint()
            try:
                candidate = self.execution.client.product('POST', self.execution.root + '/agent-builder/candidates', body)
            except Failure as error:
                if error.code != 'http_error' or error.http_status != 400:
                    raise
                rejected = True
            else:
                # Retain any unexpectedly created candidate for identity-checked cleanup.
                if not isinstance(candidate, dict) or candidate.get('agent_id') != self.agent_id:
                    raise Failure('unexpected_candidate_identity')
                self.builder.remember_candidate(candidate, self.agent_id)
                rejected = False
            self.state['candidate_proposal_pending'] = False
            self.record('手动候选拒绝错误字段 ' + defect, rejected, 'invalid_candidate_accepted', required=True)
            self.record('拒绝后没有发布智能体 ' + defect, self.absent(self.agent_path),
                        'invalid_candidate_published_agent', required=True)
        self.publish_agent(names=[], ids=[], instruction=instruction)

    def cleanup(self, *, dependencies_confirmed):
        before = len(self.execution.report['harness_errors'])
        try:
            if not dependencies_confirmed:
                raise Failure('dependent_task_unconfirmed')
            if self.builder:
                self.builder.cleanup()
            if self.state.get('candidate_proposal_pending'):
                raise Failure('candidate_proposal_outcome_unconfirmed')
            self.state['cleanup'] = 'completed' if len(self.execution.report['harness_errors']) == before else 'incomplete'
        except Exception as error:
            self.state['cleanup'] = 'incomplete'
            self.execution.report['harness_errors'].append(error_record('cleanup_builder_input', error))
        finally:
            self.execution.client.observer = self.previous_observer
            self.execution.checkpoint()
