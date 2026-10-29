"""Shared identity, transport and publication checks for case-owned resources."""
from .builder_flow import AgentBuilderFlow, ProductCaseFailure
from .client import Failure, segment


class ResourceJourney:
    def observe(self, exchange):
        if self.previous_observer:
            self.previous_observer(exchange)
        path = exchange.get('path', '')
        if path.startswith(tuple(self.execution.root + suffix for suffix in
                                 ('/' + self.kind + 's', '/agents/' + self.agent_id, '/agent-builder/'))):
            self.execution.evidence.append(self.kind + '-http.jsonl', exchange)

    def record(self, rule, passed, reason, *, required=False):
        self.checks.append({'session': self.session_id, 'rule': rule, 'passed': bool(passed),
                            'detail': '' if passed else reason})
        self.execution.report['validation'] = {'checks': self.checks}
        self.execution.checkpoint()
        if required and not passed:
            raise ProductCaseFailure(reason)

    def request(self, method, path, body=None):
        try:
            result = self.execution.client.product(method, self.execution.root + path, body)
            self.execution.evidence.append(self.kind + '-operations.jsonl', {'method': method, 'path': path, 'request': body, 'response': result})
            return result
        except Failure as error:
            if error.code != 'product_error' and (error.code != 'http_error' or error.http_status in {None, 401, 403}):
                raise
            self.record(self.kind + ' 操作被产品接受', False, error.code + ':' + str(error.http_status))
            raise ProductCaseFailure(self.kind + '_operation_failed') from None

    def absent(self, path):
        try:
            self.execution.client.product('GET', self.execution.root + path)
        except Failure as error:
            if error.code == 'http_error' and error.http_status == 404:
                return True
            raise
        return False

    def rejected(self, method, path, body, expected):
        try:
            self.execution.client.product(method, self.execution.root + path, body)
        except Failure as error:
            if error.code == 'http_error' and error.http_status not in {None, 401, 403}:
                return error.http_status == expected
            raise
        return False

    @property
    def agent_path(self):
        return '/agents/' + segment(self.agent_id)

    def selector(self):
        if not self.agent_pending:
            raise Failure('resource_agent_not_created')
        return {'agent_id': self.agent_id, 'agent_workspace_id': self.workspace}

    def publish_agent(self, *, names, ids, instruction, binding=None):
        if not self.absent(self.agent_path):
            raise Failure('resource_agent_id_exists')
        self.builder = AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id])
        resources = self.request('GET', '/agent-builder/resources')
        self.state['candidate_proposal_pending'] = True
        self.execution.checkpoint()
        candidate = self.request('POST', '/agent-builder/candidates', self.candidate_request(names, instruction, resources))
        self.builder.remember_candidate(candidate, self.agent_id)
        self.state['candidate_proposal_pending'] = False
        self.state['candidate'] = {key: candidate[key] for key in ('agent_id', 'candidate_version', 'source_digest')}
        if candidate['agent_id'] != self.agent_id:
            raise ProductCaseFailure('manual_agent_id_mismatch')
        references = [self.builder.require_resource(self.kind + 's', resource_id, workspace_id=self.workspace)
                      for resource_id in ids]
        self.agent_pending = True
        self.state['agent_id'] = self.agent_id
        self.execution.checkpoint()
        self.builder.commit(references, binding=binding)

    def candidate_request(self, names, instruction, resources):
        return {
            'mode': 'create', 'conversation_id': 'manual-' + self.execution.report['run_id'],
            'raw_advice': 'Create a synthetic resource verification Agent.', 'resources': resources,
            'candidate': {'agent_id': self.agent_id, 'name': self.agent_id,
                'description': 'Synthetic resource verification', 'model_name': self.execution.model,
                'tool_names': names if self.kind == 'tool' else [],
                'skill_names': names if self.kind == 'skill' else [],
                'knowledge_base_names': [], 'channel_bindings': [], 'agent_md': instruction,
                'change_reason': 'Synthetic resource verification.'}}

    def detach_agent_resources(self, ids):
        if self.builder:
            self.builder.cleanup()
        if self.agent_pending and not self.absent(self.agent_path):
            client = self.execution.client
            path = self.execution.root + self.agent_path
            agent = client.product('GET', path)
            if (agent.get('id') != self.agent_id or agent.get('workspace_id') != self.workspace or
                    agent.get('name') != self.agent_id):
                raise Failure('cleanup_resource_agent_identity_mismatch')
            if agent.get('status') != 'archived':
                client.product('PATCH', path, {'status': 'archived'})
            client.product('PATCH', path + '/bindings', {'binding_ops': [
                {'op': 'remove', 'kind': self.kind,
                 'ref': {'id': resource_id, 'kind': self.kind, 'workspace_id': self.workspace}}
                for resource_id in sorted(ids)]})
            bound = client.product('GET', path + '/bindings')
            # Binding detail omits an empty resource list (omitempty).
            items = bound.get(self.kind + 's', [])
            if not isinstance(items, list) or any(not isinstance(item, dict) or (
                    item.get('id') in ids and item.get('workspace_id') == self.workspace) for item in items):
                raise Failure('cleanup_resource_unbind_unconfirmed')
