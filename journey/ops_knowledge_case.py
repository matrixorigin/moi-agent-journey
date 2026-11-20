"""Replay a QA-only business question against a fixed structured-KB snapshot."""
import json
import re

from .client import Failure, segment
from .hello import discover_workspace
from .production import binding, bindings, is_production
from .knowledge_case import KnowledgeExecution
from .knowledge_sources import list_sources
from .rag_quality import citation_integrity_metric, observe_rag
from .trajectory import task_trajectory
from .version import start_version
from .benchmark_store import canonical, digest


class BenchmarkDependencyUnavailable(Exception):
    pass


def validate_sql_citations(observed, tables):
    """Validate source identity without executing SQL supplied by an agent.

    Native selection attests that MOI read that task's source. It does not
    establish that the answer is correct or expose the complete query result.
    Without that receipt, integrity is unknown rather than automatically false.
    """
    allowed = set(tables)
    for citation in observed['citations']:
        ref = citation['reference']
        table = ref.get('table') or ''
        if '.' not in table and ref.get('database'):
            table = ref['database'] + '.' + table
        citation['integrity'] = None
        citation['source_available'] = None
        if ref.get('type') != 'sql_table':
            citation['source_error'] = 'unsupported_citation_modality'
        elif (table not in allowed or not ref.get('artifact_id') or not ref.get('sql')):
            citation.update(integrity=False, source_available=False,
                            source_error='citation_identity_or_scope_mismatch')
        elif observed.get('selected_read_receipt'):
            citation.update(integrity=True, source_available=True, retrieval_link=True,
                            verification_method='native_selection_receipt_and_snapshot_catalog')
        else:
            citation['source_error'] = 'native_source_provenance_unobserved'
    known = [item['integrity'] for item in observed['citations'] if item['integrity'] is not None]
    observed['citation_integrity'] = {'valid': sum(known), 'total': len(known),
        'expected_total': len(observed['citations']), 'unavailable': len(observed['citations']) - len(known)}


class OpsKnowledgeExecution(KnowledgeExecution):
    def record_failure(self, error):
        if not isinstance(error, BenchmarkDependencyUnavailable):
            return super().record_failure(error)
        self.report['status'] = 'failed'
        self.report['failures'].append({'stage': 'benchmark_prerequisite', 'reason': str(error)})
        self.checkpoint()

    def setup(self, *, capture_version=True):
        self.root = discover_workspace(self.client, 'journey-3')
        target = self.case['ops_target']
        if is_production():
            target = binding(bindings()['operations_agents'], target['agent_id'])
        workspace = self.root.rsplit('/', 1)[1]
        self.selector = {'agent_id': target['agent_id'], 'agent_workspace_id': workspace}
        agent = self.client.product('GET', self.root + '/agents/' + segment(target['agent_id']))
        self.report['benchmark_agent_name'] = agent.get('name', target['agent_id'])
        self.allowed_model_ids = {int(value) for value in target['model_ids']}
        bindings = agent.get('binding', {})
        if (agent.get('id') != target['agent_id'] or agent.get('status') != 'active' or agent.get('workspace_id') != workspace or
                len(bindings.get('knowledge_base_refs', [])) != len(self.allowed_model_ids) or
                {int(row['id']) for row in bindings.get('knowledge_base_refs', [])} != self.allowed_model_ids or
                any(row.get('workspace_id') != workspace or
                    ('status' in row and row['status'] != 'active') for row in bindings.get('knowledge_base_refs', [])) or
                bindings.get('channel_bindings')):
            raise Failure('operations_clone_binding_mismatch')
        self.report['benchmark_agent_name'] = agent['name']
        self.report['rag'].update(target={'agent_id': target['agent_id'], 'corpora': [self.case['rag']['corpus']]},
            evaluator={'method': 'workspace_model_json_schema', 'model': self.model, 'protocol': 3})
        self.report['rag']['corpus_inventory_matches'] = False
        self.evidence.write('knowledge/agent.json', agent)
        self.binding_context = []
        # Current QA returns compact binding references without names/status.
        # Compare stable scoped IDs; display names are not reference identities.
        missing = []
        for kind in ('tool', 'skill'):
            bound = {(item['id'], item.get('workspace_id')) for item in bindings.get(kind + '_refs', [])}
            required = target.get('required_' + kind + '_refs', [])
            missing += [item['id'] for item in required if (item['id'], item['workspace_id']) not in bound]
        eligibility=self.case.get('benchmark_eligibility','knowledge_sql')
        self.report['benchmark']={'state':'eligible','eligibility':eligibility}
        if eligibility!='knowledge_sql':
            self.report['benchmark']['state']='out_of_scope'
            raise BenchmarkDependencyUnavailable('original_request_requires_capabilities_outside_the_KB_snapshot')
        if self.case.get('required_inputs_state', 'ready') != 'ready':
            self.report['benchmark']['state'] = 'blocked'
            raise BenchmarkDependencyUnavailable('historical_input_unavailable')
        if missing:
            self.report['benchmark'].update(state='blocked',missing_dependencies=sorted(missing))
            raise BenchmarkDependencyUnavailable('replica_dependencies_unavailable')
        from .benchmark_store import Store
        store = Store(self.client, self.root)
        snapshot = store.dataset(self.case['snapshot_id'])
        if snapshot['manifest_sha256'] != self.case['snapshot_manifest_sha256']:
            raise Failure('benchmark_snapshot_manifest_mismatch')
        if snapshot['state'] != 'complete':
            self.report['benchmark']['state'] = 'blocked'
            raise BenchmarkDependencyUnavailable('benchmark_snapshot_not_ready')
        inventory_matches = True
        for model_id in sorted(self.allowed_model_ids):
            sources = list_sources(self.client, model_id)
            self.evidence.write('knowledge/sources-' + str(model_id) + '.json', sources)
            active = {(row.get('db_name'), row.get('table_name')) for row in sources
                      if row.get('effective_enabled') is True and row.get('ingest_status') == 'succeeded'}
            expected = {tuple(name.split('.', 1)) for name in target['expected_enabled_tables'][str(model_id)]}
            inventory_matches &= active == expected
        self.report['rag']['corpus_inventory_matches'] = inventory_matches
        if not inventory_matches:
            self.report['benchmark']['state'] = 'blocked'
            raise BenchmarkDependencyUnavailable('benchmark_knowledge_sources_not_ready')
        if capture_version:
            start_version(self)

    def check_reference(self, config):
        if config.get('status') != 'reviewed' or not config.get('verification', {}).get('probes'):
            return {'state': 'unavailable', 'reason': 'independent_golden_not_reviewed'}
        for probe in config['verification']['probes']:
            columns, rows = probe.get('columns'), probe.get('rows')
            if (not isinstance(columns, list) or not columns or
                    any(not isinstance(name, str) or not name or name == '*' for name in columns) or
                    not isinstance(rows, list) or
                    any(not isinstance(row, list) or len(row) != len(columns) for row in rows) or
                    digest({'columns': columns, 'rows': rows}) != probe.get('facts_sha256')):
                raise Failure('independent_reference_shape_or_hash_invalid')
        from .benchmark_store import Store
        # Golden calculations use the sealed, independent offline snapshot.
        # Re-running reference SQL in QA would couple the oracle to the very
        # engine/runtime failure that the benchmark is intended to detect.
        store = Store(self.client, self.root)
        manifest=store.dataset(self.case['snapshot_id'])
        matched=(manifest['state']=='complete' and manifest['manifest_sha256']==self.case['snapshot_manifest_sha256'])
        self.evidence.write('knowledge/reference-snapshot.json',manifest)
        return {'state':'matched' if matched else 'changed',
                'method':'snapshot_manifest_identity', 'live_data_checksum_verified': False}

    def prepare_answer(self, session, turn, answer):
        path = self.evidence.root / 'events.jsonl'
        observed = observe_rag(path, answer['task_id'], self.allowed_model_ids)
        observed['retrieval_scope'] = 'selected_reads'
        observed['context_evidence'] = []
        answer['observations'] = observed
        efficiency = task_trajectory(path, self.tasks, answer['task_id'])
        efficiency['task_seconds'] = answer['seconds']
        answer['efficiency'] = efficiency
        candidate_file_ids = set()
        # SQL/CLI evidence is a different modality from document chunks. Keep
        # complete tool results when observed; never turn a preview into proof.
        for line in path.read_text().splitlines():
            event = json.loads(line)
            payload = event.get('payload', {})
            if event.get('task_id') != answer['task_id']:
                continue
            if event.get('kind') == 'tool_call':
                arguments = canonical(payload)
                if ('journey3_benchmark' in arguments.casefold() or
                        'j3-operations-reference' in arguments.casefold()):
                    answer.update(evaluation_state='unavailable', evaluation_reason='benchmark_reference_access_detected')
                    self.report['benchmark']['state'] = 'blocked'
                    return
                candidate_file_ids.update(re.findall(r'[A-Za-z0-9_-]{16,128}', arguments))
            if (event.get('kind') == 'tool_result' and payload.get('success') is True and
                    payload.get('tool_id') == 'query_sql' and payload.get('call_id') in observed['sql_calls'] and
                    not payload.get('result_truncated') and not payload.get('payload_truncated')):
                observed['retrieved_chunks'].append({'evidence_id': 'R' + str(len(observed['retrieved_chunks'])+1),
                    'source_task_id': answer['task_id'], 'content': canonical(payload), 'origin': 'observed_sql_tool_result'})
            # Ordinary shell stdout may be fabricated by the tested agent.
            # It stays in the trajectory, never in verified grounding evidence.
        from .benchmark_store import Store
        if Store(self.client, self.root).accessed_private_evidence(candidate_file_ids):
            answer.update(evaluation_state='unavailable', evaluation_reason='benchmark_evidence_file_access_detected')
            self.report['benchmark']['state'] = 'blocked'
            return
        validate_sql_citations(observed, self.case['ops_target']['tables'])
        answer['metrics'] = {'citation_integrity': citation_integrity_metric(observed)}
        answer['reference_check'] = self.check_reference(turn['rag_reference'])
        if answer['reference_check']['state'] != 'matched':
            answer.update(evaluation_state='unavailable', evaluation_reason='reference_snapshot_changed_or_unverified')
            return
        return True


def main():
    import sys
    from . import knowledge_case
    # Use the existing timeout, event capture, evaluator and cleanup lifecycle.
    return knowledge_case.main(execution_type=OpsKnowledgeExecution)


if __name__ == '__main__':
    raise SystemExit(main())
