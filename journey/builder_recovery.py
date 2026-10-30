"""Publication failure recovery and duplicate confirmation on owned candidates."""

from .builder_flow import AgentBuilderFlow
from .client import Failure, segment
from .builder_edge_journey import concurrent_products
from .owned_builder_journey import OwnedBuilderJourney


class BuilderRecoveryJourney(OwnedBuilderJourney):
    def __init__(self, execution, mode, checks):
        if mode not in {'publication_recovery', 'duplicate_commit'}:
            raise Failure('invalid_builder_recovery')
        super().__init__(execution, checks)
        self.mode = mode
        self.state['mode'] = mode

    def prepare(self):
        if not self.absent(self.agent_path):
            raise Failure('resource_agent_id_exists')
        self.builder = AgentBuilderFlow(self.execution, self.session_id, self.checks, [self.agent_id])
        instruction = 'Follow the user request. Echo only the exact code when asked.'
        self.state['candidate_proposal_pending'] = True
        self.execution.checkpoint()
        candidate = self.request('POST', '/agent-builder/candidates', self.candidate_request(
            [], instruction, self.request('GET', '/agent-builder/resources')))
        self.builder.remember_candidate(candidate, self.agent_id)
        self.state['candidate_proposal_pending'] = False
        path = '/agent-builder/candidates/' + segment(self.agent_id) + '/candidate-versions/' + segment(candidate['candidate_version'])
        body = {'source_digest': candidate['source_digest']}
        if self.mode == 'publication_recovery':
            # The public loader rejects caller-owned writes to reserved package metadata.
            self.builder.owned_published.add(self.agent_id)
            self.agent_pending = True
            self.record('发布因保留元数据冲突被拒绝', self.rejected('POST', path + '/commit',
                        {**body, 'metadata': {'agent_package_id': 'forged'}}, 400),
                        'invalid_publication_not_rejected', required=True)
            self.record('发布失败未留下半成品智能体', self.absent(self.agent_path),
                        'failed_publication_left_agent', required=True)
            self.state['candidate_proposal_pending'] = True
            self.execution.checkpoint()
            updated = self.request('POST', path + '/repropose', {**body,
                'overrides': {'agent_md': instruction + ' Publication recovered.'}})
            self.builder.remember_candidate(updated, self.agent_id)
            self.state['candidate_proposal_pending'] = False
            self.record('失败候选可修订且产生新摘要', updated['candidate_version'] != candidate['candidate_version'] and
                        updated['source_digest'] != candidate['source_digest'],
                        'failed_candidate_not_revisable', required=True)
        else:
            committed = self.concurrent_commit(path + '/commit', body, candidate)
        self.agent_pending = True
        self.state['agent_id'] = self.agent_id
        if self.mode == 'duplicate_commit':
            self.builder.verify_commit(committed)
        else:
            self.builder.commit()
        versions = self.request('GET', self.agent_path + '/versions')
        self.record('确认后仅产生一个可运行版本',
            len(versions.get('items', [])) == 1 and versions['items'][0].get('status') == 'runnable' and
            versions.get('lineage', {}).get('default_version') == self.builder.created['version'],
            'publication_created_duplicate_versions', required=True)
        before = versions
        current = self.builder.current
        repeat_path = '/agent-builder/candidates/' + segment(self.agent_id) + '/candidate-versions/' + segment(current['candidate_version']) + '/commit'
        self.record('已确认候选再次提交被拒绝', self.rejected('POST', repeat_path,
                    {'source_digest': current['source_digest']}, 409), 'repeat_commit_not_rejected', required=True)
        self.record('再次确认不会改变已发布版本链', self.request('GET', self.agent_path + '/versions') == before,
                    'repeat_commit_mutated_versions', required=True)

    def concurrent_commit(self, path, body, candidate):
        # Publication is authorized for this absent identity, even if a response is lost.
        self.builder.owned_published.add(self.agent_id)
        self.agent_pending = True
        self.execution.checkpoint()
        responses = concurrent_products(self.execution.client,
            [('POST', self.execution.root + path, body)] * 2, self.observe)
        successes, conflicts = [], 0
        invalid_statuses = []
        for status, result, error in responses:
            if error:
                if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                    raise error
                if error.http_status == 409:
                    conflicts += 1
                else:
                    invalid_statuses.append(error.http_status)
            else:
                successes.append(result)
        self.record('并发确认至少一次成功且所有成功响应指向同一版本',
            not invalid_statuses and bool(successes) and len(successes) + conflicts == 2 and all(
                item.get('agent_id') == self.agent_id and item.get('workspace_id') == self.workspace and
                item.get('source_digest') == candidate['source_digest'] and
                item.get('load_version') == candidate.get('load_version') for item in successes),
            'concurrent_commit_mismatch:' + str(invalid_statuses), required=True)
        return successes[0]
