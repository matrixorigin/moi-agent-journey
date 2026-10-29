"""Reject malformed manual proposals, then publish and use a case-owned Agent."""
import copy

from .builder_flow import AgentBuilderFlow
from .client import Failure
from .owned_builder_journey import OwnedBuilderJourney


class BuilderInputJourney(OwnedBuilderJourney):
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
