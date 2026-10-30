"""Reject stale edits of an already published Agent without losing newer changes."""
from .agent_builder import candidate_matches
from .builder_flow import same_published_config
from .client import Failure, segment
from .owned_builder_journey import OwnedBuilderJourney


class PublishedConflictJourney(OwnedBuilderJourney):
    def current(self, version):
        return self.request('GET', '/agent-builder/agents/' + segment(self.agent_id) +
                            '/versions/' + segment(version) + '/current-agent')

    def revision_body(self, current, version, changes):
        return {'mode': 'revise', 'conversation_id': 'manual-' + self.execution.report['run_id'],
            'source_agent_id': self.agent_id, 'source_agent_workspace_id': self.workspace,
            'source_version': version, 'raw_advice': 'Apply only the requested edit.',
            'resources': self.request('GET', '/agent-builder/resources'),
            'changes': {'base_revision': current['base_revision'], 'change_reason': 'Stale editor regression', **changes}}

    def propose(self, body, *, reject=False):
        self.state['candidate_proposal_pending'] = True
        self.execution.checkpoint()
        try:
            candidate = self.execution.client.product('POST', self.execution.root + '/agent-builder/candidates', body)
        except Failure as error:
            if error.code != 'http_error' or error.http_status in {None, 401, 403}:
                raise
            if not reject:
                if error.http_status in {400, 409}:
                    self.state['candidate_proposal_pending'] = False
                self.record('当前版本编辑产生有效且同身份的修订候选', False,
                            'valid_published_edit_rejected:' + str(error.http_status), required=True)
            self.state['candidate_proposal_pending'] = False
            self.record('过期编辑明确返回版本冲突', error.http_status == 409,
                        'stale_published_edit_status:' + str(error.http_status))
            return None
        identity = (candidate_matches(candidate, self.agent_id, self.workspace, mode='revise') and
                    candidate.get('agent_id') == self.agent_id)
        if identity:
            self.builder.revisions[self.agent_id] = (candidate, self.agent_id)
            self.state['candidate_proposal_pending'] = False
        self.record('过期编辑明确返回版本冲突' if reject else '当前版本编辑产生有效且同身份的修订候选',
                    False if reject else identity and candidate.get('status') == 'valid',
                    'stale_published_edit_accepted' if reject else 'published_revision_invalid', required=not reject)
        if not identity:
            raise Failure('unexpected_revision_candidate_identity')
        return candidate

    def commit_revision(self, current, version, changes, *, preserve_description):
        body = self.revision_body(current, version, changes)
        candidate = self.propose(body)
        self.builder.current = candidate
        self.builder.commit()
        version = self.builder.created['version']
        after = self.current(version)
        self.record('修订仅改变请求字段并保留其他配置',
            all(after.get(key) == value for key, value in changes.items()) and
            same_published_config(current, after, keep_description=preserve_description, allow_instruction_change=True),
            'published_edit_lost_configuration', required=True)
        return after, version

    def prepare(self):
        suffix = self.execution.report['run_id'][:8]
        self.publish_agent(names=[], ids=[], instruction='Always respond only with BASE-' + suffix)
        version0 = self.builder.created['version']
        original = self.current(version0)
        stale = self.revision_body(original, version0, {'description': 'Stale editor must not win.'})
        first, version1 = self.commit_revision(original, version0,
            {'description': 'FIRST-' + suffix, 'agent_md': 'Always respond only with FIRST-' + suffix}, preserve_description=False)
        versions = self.request('GET', self.agent_path + '/versions')
        for name, body in [('source_version', stale), ('base_revision', {
                **stale, 'source_version': version1})]:
            self.state['conflict_field'] = name
            self.propose(body, reject=True)
            self.record('拒绝过期编辑后版本链不变 ' + name,
                self.request('GET', self.agent_path + '/versions') == versions, 'stale_edit_changed_versions', required=True)
            self.record('拒绝过期编辑后已发布配置不变 ' + name, self.current(version1) == first,
                        'stale_edit_overwrote_published_configuration', required=True)
        code = 'LATEST-' + suffix
        latest, version2 = self.commit_revision(first, version1,
            {'agent_md': 'Always respond only with ' + code}, preserve_description=True)
        saved = self.request('GET', self.agent_path + '/versions')
        self.record('刷新后成功修改且保留完整三版本历史',
            {item.get('version') for item in saved.get('items', [])} == {version0, version1, version2} and
            len(saved.get('items', [])) == 3 and saved.get('lineage', {}).get('default_version') == version2 and
            self.current(version0) == original and self.current(version1) == first and
            latest.get('description') == first.get('description'), 'published_conflict_version_history_mismatch', required=True)
        return {'revision_code': code}
