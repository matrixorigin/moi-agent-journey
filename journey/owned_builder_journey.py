"""Lifecycle and identity-bound cleanup for manually proposed case-owned Agents."""
from .client import Failure, error_record
from .resource_journey import ResourceJourney


class OwnedBuilderJourney(ResourceJourney):
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
