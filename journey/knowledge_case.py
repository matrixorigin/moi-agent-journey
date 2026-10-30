"""Evaluate fixed Q&A against existing, bound agents without changing their resources."""
import argparse
import copy
import json
import os
import shutil
import signal
import time
from pathlib import Path

from .a2a import final_reply
from .catalog import ROOT, load_case
from .client import Client, Failure, error_record, segment
from .evidence import Evidence, configured_secrets, utc_now
from .hello import discover
from .knowledge_sources import list_sources, read_document
from .rag_quality import judge_prompt, observe_rag, parse_verdict, summarize_verdict
from .run_case import Execution
from .trajectory import task_trajectory
from .version import finish_version, start_version


class KnowledgeExecution(Execution):
    def setup(self, *, capture_version=True):
        self.root, self.judge_selector = discover(self.client, self.case['workspace'])
        self.report['rag']['evaluator'] = {'method': 'independent_MOMO_conversations',
                                          'model': self.model, 'selector': self.judge_selector}
        catalog = json.loads((ROOT / 'datasets/journey-3/corpora.json').read_text())
        corpus = catalog[self.case['rag']['corpus']]
        self.corpus = corpus
        target = self.case.get('knowledge_target', {'agent_id': corpus['agent_id'], 'corpora': [self.case['rag']['corpus']]})
        names = target.get('corpora')
        if (not isinstance(target.get('agent_id'), str) or not target['agent_id'] or
                not isinstance(names, list) or not names or len(set(names)) != len(names) or
                any(name not in catalog for name in names) or self.case['rag']['corpus'] not in names):
            raise Failure('invalid_knowledge_target')
        self.bound_corpora = [catalog[name] for name in names]
        self.allowed_model_ids = {item['model_id'] for item in self.bound_corpora}
        workspace = self.root.rsplit('/', 1)[1]
        self.selector = {'agent_id': target['agent_id'], 'agent_workspace_id': workspace}
        agent = self.client.product('GET', self.root + '/agents/' + segment(target['agent_id']))
        bindings = agent.get('binding', {}).get('knowledge_base_refs', [])
        if (agent.get('id') != target['agent_id'] or agent.get('workspace_id') != workspace or
                agent.get('status') != 'active' or len(bindings) != len(self.allowed_model_ids) or
                {item.get('id') for item in bindings} != {str(mid) for mid in self.allowed_model_ids} or
                any(item.get('workspace_id') != workspace or item.get('status') != 'active' for item in bindings)):
            raise Failure('knowledge_agent_binding_mismatch')
        self.evidence.write('knowledge/agent.json', agent)
        self.binding_context = [{'evidence_id': 'B' + str(index + 1), 'origin': 'verified_agent_binding',
            'content': json.dumps({'semantic_model_id': item['id'], 'name': item.get('name'),
                                   'workspace_id': item['workspace_id']}, ensure_ascii=False)}
            for index, item in enumerate(bindings)]
        self.report['rag']['target'] = target
        self.sources = []
        inventory_matches = True
        for bound in self.bound_corpora:
            sources = list_sources(self.client, bound['model_id'])
            inventory_matches &= sorted(s['row_id'] for s in sources) == sorted(bound['source_ids'])
            self.sources.extend(sources)
        self.evidence.write('knowledge/sources.json', self.sources)
        self.report['rag']['corpus_inventory_matches'] = inventory_matches
        if capture_version:
            start_version(self)

    def validate_sources(self, observed):
        sources = {(str(item['model_id']), item['row_id']): item for item in self.sources}
        documents = {}
        for citation in observed['citations']:
            ref = citation['reference']
            source_id = ref.get('source_row_id')
            model_id = str(ref.get('semantic_model_id'))
            source = sources.get((model_id, source_id))
            citation['source_available'] = False
            if (source is None or model_id not in {str(mid) for mid in self.allowed_model_ids} or
                    ref.get('file_id') != source.get('kb_file_id') or source.get('effective_enabled') is not True):
                citation['source_error'] = 'citation_source_identity_or_scope_mismatch'
                citation['integrity'] = False
                continue
            try:
                key = (model_id, source_id)
                if key not in documents:
                    documents[key] = read_document(self.client, int(model_id), source_id)
                document = documents[key]
                cited_ids = [ref.get('chunk_id')] + (ref.get('chunk_ids') if isinstance(ref.get('chunk_ids'), list) else [])
                available = {row.get('chunk_id') or row['segment_id']: row for row in document['segments']
                             if row.get('enabled') is True}
                wanted = {value for value in cited_ids if isinstance(value, str) and value}
                citation['source_available'] = bool(wanted) and wanted.issubset(available)
                citation['integrity'] = citation['source_available'] and citation['retrieval_link'] is not False
                for chunk_id in sorted(wanted & available.keys()):
                    evidence_id = 'C' + str(len(observed['citation_chunks']) + 1)
                    observed['citation_chunks'].append({'evidence_id': evidence_id, 'source_row_id': source_id,
                        'semantic_model_id': int(model_id),
                        'file_id': source['kb_file_id'], 'chunk_id': chunk_id, 'content': available[chunk_id]['content']})
                    citation['source_evidence_ids'].append(evidence_id)
            except Failure as error:
                if error.code in {'case_timeout', 'runner_canceled', 'deadline_exceeded'}:
                    raise
                citation['source_error'] = error_record('citation_open', error)
                citation['integrity'] = False
        for (model_id, source_id), document in documents.items():
            selected = {item['chunk_id'] for item in observed['citation_chunks']
                        if item['source_row_id'] == source_id and str(item['semantic_model_id']) == model_id}
            self.evidence.write('knowledge/cited/' + model_id + '/' + segment(source_id) + '.json', {
                'source': document['source'], 'etag': document.get('etag'),
                'total_segments': document['segment_status']['total'],
                'cited_segments': [row for row in document['segments'] if (row.get('chunk_id') or row['segment_id']) in selected]})
        observed['citation_integrity']['valid'] = sum(item['integrity'] for item in observed['citations'])

    def check_reference(self, config):
        state, records = 'matched', []
        references = {(self.corpus['model_id'], item['source_id']): item['etag'] for item in config.get('evidence', [])}
        if config.get('verify_whole_corpus'):
            for bound in self.bound_corpora:
                references.update({(bound['model_id'], sid): etag for sid, etag in bound['source_etags'].items()})
        for (model_id, source_id), expected_etag in references.items():
            try:
                page = self.client.product('GET', f'/semantic-models/{model_id}/sources/' +
                                           segment(source_id) + '/document?page_size=1', expected_code='OK')
            except Failure as error:
                if error.code in {'case_timeout', 'runner_canceled', 'deadline_exceeded'}:
                    raise
                record = {'model_id': model_id, 'source_id': source_id, 'state': 'unavailable', 'error': error_record('reference_read', error)}
                self.evidence.append('knowledge/reference-checks.jsonl', record)
                records.append(record)
                return {'state': 'unavailable', 'sources': records}
            valid = (page.get('source', {}).get('row_id') == source_id and
                     page.get('source', {}).get('model_id') == model_id and
                     page.get('source', {}).get('effective_enabled') is True and
                     page.get('etag') == expected_etag and expected_etag)
            record = {'model_id': model_id, 'source_id': source_id, 'expected_etag': expected_etag,
                      'observed_etag': page.get('etag'), 'matched': bool(valid)}
            self.evidence.append('knowledge/reference-checks.jsonl', record)
            records.append(record)
            if not valid:
                state = 'changed'
        return {'state': state, 'sources': records}

    def evaluate(self, session, turn, answer):
        config = turn['rag_reference']
        context = []
        for task in self.tasks:
            if task['task_id'] == answer['task_id']:
                break
            if task['conversation_id'] == answer['conversation_id'] and not task['phase'].startswith('judge:'):
                context.append(task['task_id'])
        observed = observe_rag(self.evidence.root / 'events.jsonl', answer['task_id'], self.allowed_model_ids, context)
        observed['context_evidence'] = self.binding_context
        efficiency = task_trajectory(self.evidence.root / 'events.jsonl', self.tasks, answer['task_id'])
        efficiency['task_seconds'] = answer['seconds']
        answer['efficiency'] = efficiency
        self.validate_sources(observed)
        answer['observations'] = observed
        integrity = observed['citation_integrity']
        answer['metrics'] = {'citation_integrity': {**integrity,
            'rate': integrity['valid'] / integrity['total'] if integrity['total'] else None}}
        required = config.get('citation_required', True)
        checks = self.report['validation']['checks']
        checks.append({'session': session['id'], 'rule': turn['phase'] + ':引用属于当前任务、知识库且来源可读取',
                       'passed': all(item['integrity'] for item in observed['citations']) and
                                 (bool(observed['citations']) or not required),
                       'detail': '' if observed['citations'] else 'no_structured_citations'})
        if config['status'] != 'reviewed' or not self.report['rag']['corpus_inventory_matches']:
            answer.update(evaluation_state='unavailable', evaluation_reason='reference_not_verified_or_corpus_changed')
            return
        answer['reference_check'] = self.check_reference(config)
        if answer['reference_check']['state'] != 'matched':
            answer.update(evaluation_state='unavailable', evaluation_reason=answer['reference_check'])
            return
        prompt = judge_prompt(turn['prompt'], answer['text'], config, observed, efficiency)
        cid = self.conversation('judge-' + session['id'] + '-' + turn['phase'], selector=self.judge_selector)
        _, task = self.send(cid, 'judge:' + session['id'] + ':' + turn['phase'], prompt)
        verdict = parse_verdict(final_reply(task), answer['text'], config, observed, efficiency)
        self.apply_verdict(session, turn, answer, verdict)

    def apply_verdict(self, session, turn, answer, verdict):
        """Use the same accounting for live verdicts and revalidated saved verdicts."""
        config, observed, efficiency = turn['rag_reference'], answer['observations'], answer['efficiency']
        checks = self.report['validation']['checks']
        efficiency['review'] = verdict['efficiency']
        answer.update(evaluation_state='scored', verdict=verdict, metrics=summarize_verdict(verdict, observed))
        expected_sources = config.get('expected_source_ids', [])
        actual = {item.get('source_row_id') for item in observed['retrieved_chunks']}
        answer['annotated_source_recall'] = {'retrieved': len(set(expected_sources) & actual), 'total': len(expected_sources),
            'rate': len(set(expected_sources) & actual) / len(expected_sources) if expected_sources else None}
        for criterion in verdict['criteria']:
            checks.append({'session': session['id'], 'rule': turn['phase'] + ':' + criterion['id'],
                           'passed': criterion['passed'], 'detail': criterion['reason'],
                           'evidence_quote': criterion.get('answer_quote')})
        for index, claim in enumerate(verdict['claims'] if observed['retrieved_chunks'] else []):
            checks.append({'session': session['id'], 'rule': turn['phase'] + ':检索依据支持主张' + str(index + 1),
                           'passed': claim['supported'], 'detail': claim['reason']})
        for citation in verdict['citations']:
            checks.append({'session': session['id'], 'rule': turn['phase'] + ':引用支持结论' + str(citation['index']),
                           'passed': citation['supported'], 'detail': citation['reason']})

    def grade_answers(self, answers):
        self.report['stage'] = 'semantic_judge'
        budget = self.case['evaluation_timeout_seconds']
        signal.alarm(budget)
        self.client.deadline = time.monotonic() + budget
        started = time.monotonic()
        try:
            for session, turn, answer in answers:
                try:
                    self.evaluate(session, turn, answer)
                except Exception as error:
                    answer.update(evaluation_state='unavailable', evaluation_reason=error_record('semantic_judge', error))
                    self.report['harness_errors'].append(error_record('semantic_judge', error))
                self.checkpoint()
        finally:
            self.report['semantic_judge_seconds'] = self.report.get('semantic_judge_seconds', 0) + round(time.monotonic() - started, 3)
            signal.alarm(0)
        checks = self.report['validation']['checks']
        self.report['status'] = ('error' if self.report['harness_errors'] else
                                 'passed' if all(item['passed'] for item in checks) else 'failed')
        if self.report['status'] == 'failed':
            self.report['failures'].append({'stage': 'validation', 'reason': 'acceptance_checks_failed'})

    @staticmethod
    def delivery_check(answer):
        return {'session': answer['session'], 'rule': answer['phase'] + ':正常完成并返回非空最终回答',
                'passed': bool(answer['text'].strip()), 'detail': '' if answer['text'].strip() else 'empty_final_answer'}

    def reevaluate(self, saved):
        self.report = copy.deepcopy(saved)
        self.tasks, self.conversations = self.report['tasks'], self.report['conversations']
        self.started -= self.report.get('elapsed_seconds', 0)
        history = self.report.setdefault('evaluation_replays', [])
        history.append({'started_at': utc_now(), 'original_task_ids': [task['task_id'] for task in self.tasks
            if not task['phase'].startswith('judge:')], 'previous_harness_errors': self.report.get('harness_errors', [])})
        self.report['harness_errors'] = [error for error in self.report.get('harness_errors', [])
                                        if error.get('stage') not in {'semantic_judge', 'trajectory_capture'}]
        self.report['failures'] = [error for error in self.report.get('failures', []) if error.get('reason') != 'acceptance_checks_failed']
        self.report['validation'] = {'checks': [self.delivery_check(answer) for answer in self.report['rag']['answers']]}
        self.report['status'] = 'running'
        try:
            self.setup(capture_version=False)
            by_phase = {(session['id'], turn['phase']): (session, turn) for session in self.case['sessions']
                        for turn in session['turns'] if 'rag_reference' in turn}
            answers = []
            for answer in self.report['rag']['answers']:
                key = (answer['session'], answer['phase'])
                if key in by_phase:
                    for field in ('verdict', 'metrics', 'evaluation_reason', 'observations', 'efficiency', 'reference_check'):
                        answer.pop(field, None)
                    answer['evaluation_state'] = 'pending'
                    session, turn = by_phase[key]
                    answers.append((session, turn, answer))
            self.grade_answers(answers)
        except Exception as error:
            self.record_failure(error)
        finally:
            self.finalize()
            self.capture_trajectories()
            history[-1]['finished_at'] = utc_now()
            self.checkpoint()
        return self.report

    def run(self):
        self.report['rag'] = {'corpus': self.case['rag']['corpus'], 'answers': [],
                              'model': self.model,
                              'expected_answers': sum('rag_reference' in turn for session in self.case['sessions']
                                                      for turn in session['turns']),
                              'comparison_group': self.case['rag'].get('comparison_group')}
        self.report['validation'] = {'checks': []}
        answers = []
        try:
            self.setup()
            for session in self.case['sessions']:
                self.report['stage'] = 'session:' + session['id']
                cid = self.conversation(session['id'])
                delivered = {'id': session['id'], 'conversation_id': cid, 'answer_delivered': False}
                self.report['sessions'].append(delivered)
                for turn in session['turns']:
                    entry, task = self.send(cid, session['id'] + ':' + turn['phase'], turn['prompt'])
                    text = final_reply(task)
                    answer = {'session': session['id'], 'phase': turn['phase'], 'question': turn['prompt'],
                              'text': text, 'conversation_id': cid, 'task_id': entry['task_id'],
                              'trace_id': entry.get('trace_id'), 'seconds': entry['seconds'],
                              'evaluation_state': 'pending' if 'rag_reference' in turn else 'context_only'}
                    self.report['rag']['answers'].append(answer)
                    self.evidence.write('sessions/' + session['id'] + '/' + turn['phase'] + '-reply.json', answer)
                    self.report['validation']['checks'].append(self.delivery_check(answer))
                    delivered.update(final_task_id=entry['task_id'], answer_delivered=bool(text.strip()))
                    if 'rag_reference' in turn:
                        answers.append((session, turn, answer))
                    self.checkpoint()
            self.report['task_seconds'] = round(time.monotonic() - self.started, 3)
            self.grade_answers(answers)
        except Exception as error:
            self.record_failure(error)
        finally:
            for answer in self.report['rag']['answers']:
                if answer['evaluation_state'] == 'pending':
                    answer.update(evaluation_state='unavailable', evaluation_reason='task_sequence_not_completed')
            finish_version(self)
            self.finalize()
            self.capture_trajectories()
        return self.report

    def capture_trajectories(self):
        events = self.evidence.root / 'events.jsonl'
        if events.exists():
            try:
                self.report['rag']['trajectories'] = [
                    {'task_id': task['task_id'], 'phase': task['phase'], 'state': task['state'],
                     'seconds': task.get('seconds'), **task_trajectory(events, self.tasks, task['task_id'])}
                    for task in self.tasks if not task['phase'].startswith('judge:')]
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.report['harness_errors'].append(error_record('trajectory_capture', error))
            self.checkpoint()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case_id')
    parser.add_argument('--agent-id')
    parser.add_argument('--bound-corpora', nargs='+')
    parser.add_argument('--evaluate-existing', type=Path,
                        help='Evaluate saved answers in a new result directory without resubmitting tested tasks')
    args = parser.parse_args()
    case = load_case(args.case_id)
    if bool(args.agent_id) != bool(args.bound_corpora):
        parser.error('--agent-id and --bound-corpora must be supplied together')
    if args.agent_id:
        case['knowledge_target'] = {'agent_id': args.agent_id, 'corpora': args.bound_corpora}
    saved = None
    if args.evaluate_existing:
        source = args.evaluate_existing.resolve()
        saved = json.loads((source / 'result.json').read_text())
        prior_case = json.loads((source / 'execution-config.json').read_text())
        if (saved.get('case_id') != case['id'] or saved.get('workspace') != case['workspace'] or
                prior_case != case or not saved.get('capture_complete')):
            raise Failure('invalid_saved_knowledge_execution')
        shutil.copytree(source, Path('results') / case['id'])
    evidence = Evidence(Path('results') / case['id'], configured_secrets())
    if saved is not None:
        evidence.write('evaluation-history/' + str(len(saved.get('evaluation_replays', [])) + 1) + '.json', saved)
    evidence.write('execution-config.json', case)
    def timeout(signum, _frame):
        raise Failure('case_timeout' if signum == signal.SIGALRM else 'runner_canceled')
    signal.signal(signal.SIGALRM, timeout)
    signal.signal(signal.SIGTERM, timeout)
    signal.signal(signal.SIGINT, timeout)
    signal.alarm(case['timeout_seconds'])
    try:
        client = Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], timeout=case['timeout_seconds'],
                        observer=evidence.observe_transport)
        execution = KnowledgeExecution(client, case, evidence, Path('work'), case['model'])
        report = execution.reevaluate(saved) if saved is not None else execution.run()
    except (KeyError, Failure) as error:
        signal.alarm(0)
        evidence.write('result.json', {'case_id': case['id'], 'workspace': case['workspace'], 'title': case['title'],
            'status': 'error', 'failures': [], 'tasks': [], 'harness_errors': [error_record('configuration', error)]})
        return 1
    print(json.dumps({'case_id': case['id'], 'status': report['status'], 'task_seconds': report['task_seconds'],
                      'task_count': len(report['tasks']), 'harness_errors': len(report['harness_errors'])}))
    return 1 if report['harness_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
