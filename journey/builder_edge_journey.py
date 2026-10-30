"""Exercise real candidate races and existing-name conflicts without an LLM proposer."""
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from .agent_builder import candidate_matches
from .builder_flow import AgentBuilderFlow
from .client import Client, Failure, segment
from .owned_builder_journey import OwnedBuilderJourney


def concurrent_products(parent, requests, observer):
    """Send independent requests together; replay evidence on the owning thread."""
    barrier = Barrier(len(requests))
    def request(spec):
        method, path, body = spec
        exchanges = []
        client = Client(parent.base_url, parent.headers['X-API-Key'],
                        timeout=max(.1, parent.deadline - time.monotonic()), observer=exchanges.append)
        client.headers = dict(parent.headers)
        barrier.wait(timeout=10)
        try:
            return 200, client.product(method, path, body), None, exchanges
        except Failure as error:
            return error.http_status, None, error, exchanges
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        responses = list(pool.map(request, requests))
    for _, _, _, exchanges in responses:
        for exchange in exchanges:
            observer(exchange)
    return [(status, value, error) for status, value, error, _ in responses]


class BuilderEdgeJourney(OwnedBuilderJourney):
    def __init__(self, execution, mode, checks):
        if mode not in {'race', 'name_conflict'}:
            raise Failure('invalid_builder_edge_case')
        super().__init__(execution, checks)
        self.mode, self.previous_builder = mode, None
        self.state['mode'] = mode

    def prepare(self):
        instruction = 'Follow the user request. Echo only the exact code when asked.'
        if self.mode == 'name_conflict':
            self.publish_agent(names=[], ids=[], instruction=instruction)
            self.previous_builder = self.builder
            original_path = self.agent_path
            original = self.request('GET', original_path)
            self.agent_id += '-new'
            self.agent_pending, self.builder = False, AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id, original['name']])
            if not self.absent(self.agent_path):
                raise Failure('resource_agent_id_exists')
            body = self.candidate_request([], instruction, self.request('GET', '/agent-builder/resources'))
            body['candidate']['name'] = original['name']
            self.state['candidate_proposal_pending'] = True
            self.execution.checkpoint()
            try:
                unexpected = self.execution.client.product('POST', self.execution.root + '/agent-builder/candidates', body)
            except Failure as error:
                if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                    raise
                rejected = error.http_status == 409
                if error.http_status != 409:
                    self.record('已有智能体名称的创建请求被拒绝', False, 'duplicate_name_http:' + str(error.http_status), required=True)
            else:
                if not candidate_matches(unexpected, original['name'], self.workspace) or unexpected['agent_id'] != self.agent_id:
                    raise Failure('unexpected_candidate_identity')
                self.builder.seen[self.agent_id] = ({key: unexpected[key] for key in
                    ('agent_id', 'candidate_version', 'source_digest', 'status')}, original['name'])
                rejected = False
            self.state['candidate_proposal_pending'] = False
            self.record('已有智能体名称的创建请求被拒绝', rejected, 'duplicate_agent_name_accepted', required=True)
            self.record('冲突后原智能体完整保留且新对象未发布',
                self.request('GET', original_path) == original and self.absent(self.agent_path),
                'duplicate_name_mutated_agent', required=True)
            self.publish_agent(names=[], ids=[], instruction=instruction)
            return
        if not self.absent(self.agent_path):
            raise Failure('resource_agent_id_exists')
        self.builder = AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id])
        self.state['candidate_proposal_pending'] = True
        self.execution.checkpoint()
        old = self.request('POST', '/agent-builder/candidates',
            self.candidate_request([], instruction, self.request('GET', '/agent-builder/resources')))
        self.builder.remember_candidate(old, self.agent_id)
        self.state['candidate_proposal_pending'] = False
        prefix = '/agent-builder/candidates/' + segment(self.agent_id) + '/candidate-versions/'
        path = self.execution.root + prefix + segment(old['candidate_version']) + '/repropose'
        self.state['candidate_proposal_pending'] = True
        self.execution.checkpoint()
        responses = concurrent_products(self.execution.client, [
            ('POST', path, {'source_digest': old['source_digest'],
             'overrides': {'agent_md': instruction + ' Revision ' + str(index)}}) for index in (1, 2)], self.observe)
        winners = []
        for status, candidate, error in responses:
            if candidate is not None:
                if not candidate_matches(candidate, self.agent_id, self.workspace) or candidate['agent_id'] != self.agent_id:
                    raise Failure('unexpected_candidate_identity')
                winners.append(candidate)
                self.builder.seen[self.agent_id] = ({key: candidate[key] for key in
                    ('agent_id', 'candidate_version', 'source_digest', 'status')}, self.agent_id)
            elif error and (error.code != 'http_error' or status in {None, 401, 403}):
                raise error
        self.state['candidate_proposal_pending'] = any(status not in {200, 409} for status, *_ in responses)
        self.record('并发修改只有一个成功且另一请求返回冲突', sorted(status for status, *_ in responses) == [200, 409],
                    'candidate_concurrent_replace_mismatch', required=True)
        updated = winners[0]
        self.builder.remember_candidate(updated, self.agent_id)
        self.record('替代候选保留身份并生成新版本和摘要', updated['candidate_version'] != old['candidate_version'] and
                    updated['source_digest'] != old['source_digest'], 'candidate_race_identity_mismatch', required=True)
        self.record('并发修改后的旧候选不能发布', self.rejected('POST', prefix + segment(old['candidate_version']) + '/commit',
                    {'source_digest': old['source_digest']}, 409), 'stale_candidate_commit_accepted', required=True)
        self.record('拒绝旧版本后没有发布智能体', self.absent(self.agent_path), 'stale_candidate_published', required=True)
        self.agent_pending = True
        self.state['agent_id'] = self.agent_id
        self.builder.commit()

    def cleanup(self, *, dependencies_confirmed):
        if dependencies_confirmed and self.previous_builder:
            self.previous_builder.cleanup()
        super().cleanup(dependencies_confirmed=dependencies_confirmed)
