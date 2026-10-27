"""Run short, real Agent conversations with deterministic checks and QA evidence."""
import argparse
import json
import os
import re
import signal
import uuid
from pathlib import Path

from .a2a import final_reply
from .agent_builder import check_candidate_cancel
from .catalog import fixture_path, load_case
from .client import Client, Failure
from .evidence import Evidence, configured_secrets
from .hello import discover
from .run_case import Execution
from .search_checks import evaluate_search


def case_variables(case):
    names = case.get('random_tokens', [])
    if (not isinstance(names, list) or len(set(names)) != len(names) or
            any(not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name) for name in names)):
        raise Failure('invalid_case_variables')
    return {name: 'J2_' + uuid.uuid4().hex[:16].upper() for name in names}


def check_reply(session_id, turn, reply, variables):
    checks = [{'session': session_id, 'rule': turn['phase'] + ':有最终回复',
               'passed': bool(reply.strip()), 'detail': '' if reply.strip() else 'empty_final_reply'}]
    for assertion in turn.get('assertions', []):
        kind = assertion['type']
        if kind not in {'contains', 'not_contains'} or not isinstance(assertion.get('value'), str):
            raise Failure('invalid_case_assertion')
        expected = assertion['value'].format_map(variables)
        if not expected:
            raise Failure('empty_expected_value')
        matched = expected in reply
        passed = matched if kind == 'contains' else not matched
        checks.append({'session': session_id, 'rule': assertion['rule'], 'passed': passed,
                       'detail': '' if passed else ('missing_expected_value' if kind == 'contains' else 'unexpected_value')})
    return checks


class FunctionalExecution(Execution):
    def run(self):
        current_search = None
        try:
            self.root, self.selector = discover(self.client, self.case['workspace'])
            variables = case_variables(self.case)
            self.evidence.write('parameters.json', variables)
            fixtures = {item['path']: item for item in self.case.get('fixtures', [])}
            if len(fixtures) != len(self.case.get('fixtures', [])):
                raise Failure('duplicate_fixture')
            uploaded = {}
            checks = []
            session_ids = set()
            for session in self.case['sessions']:
                if (not isinstance(session.get('id'), str) or
                        not re.fullmatch(r'[a-z][a-z0-9_-]*', session['id']) or session['id'] in session_ids):
                    raise Failure('invalid_session_id')
                session_ids.add(session['id'])
                self.report['stage'] = 'session:' + session['id']
                cid = self.conversation(session['id'])
                final_entry = None
                candidate_verified = False
                phases = set()
                for turn in session['turns']:
                    if (not isinstance(turn.get('phase'), str) or
                            not re.fullmatch(r'[a-z][a-z0-9_-]*', turn['phase']) or turn['phase'] in phases):
                        raise Failure('invalid_turn_phase')
                    phases.add(turn['phase'])
                    current_search = session['id'] if turn.get('search_evidence') is True else None
                    attachments = []
                    for name in turn.get('attachments', []):
                        if name not in fixtures:
                            raise Failure('unknown_fixture')
                        if name not in uploaded:
                            upload = self.client.upload(self.root, fixture_path(self.case, fixtures[name]))
                            self.uploads.append(upload)
                            self.evidence.write('uploads.json', self.uploads)
                            self.report.setdefault('retained_inputs', []).append({
                                'fixture': name, 'file_id': upload['file_id'], 'reason': 'conversation_attachment'})
                            uploaded[name] = upload
                        attachments.append(uploaded[name])
                    prompt = turn['prompt'].format_map(variables)
                    final_entry, task = self.send(cid, session['id'] + ':' + turn['phase'], prompt, attachments)
                    reply = final_reply(task)
                    self.evidence.write('sessions/' + session['id'] + '/replies/' + turn['phase'] + '.json',
                                        {'task_id': final_entry['task_id'], 'text': reply})
                    checks.extend(check_reply(session['id'], turn, reply, variables))
                    if current_search:
                        search_checks, blocked = evaluate_search(self.evidence.root / 'events.jsonl',
                                                                  final_entry['task_id'], reply, session['id'])
                        checks.extend(search_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    self.report['validation'] = {'checks': checks}
                    self.checkpoint()
                    action = turn.get('action')
                    if action:
                        if action.get('type') != 'reject_invalid_digest_and_cancel':
                            raise Failure('invalid_case_action')
                        name = action['agent_name'].format_map(variables)
                        candidate_verified = check_candidate_cancel(self, task, final_entry['task_id'],
                                                                     name, checks, session['id'])
                if final_entry is None:
                    raise Failure('session_has_no_turns')
                self.report['sessions'].append({'id': session['id'], 'conversation_id': cid,
                    'final_task_id': final_entry['task_id'], 'answer_delivered': True,
                    'candidate_verified': candidate_verified})
                self.checkpoint()
            if not checks:
                raise Failure('quality_checks_missing')
            if all(check['passed'] for check in checks):
                self.report['status'] = 'passed'
            else:
                self.report['status'] = 'failed'
                self.report['failures'].append({'stage': 'validation', 'reason': 'acceptance_checks_failed'})
            self.report['stage'] = 'complete'
        except Exception as error:
            self.record_failure(error)
            if current_search and self.tasks and (self.evidence.root / 'events.jsonl').exists():
                try:
                    _, blocked = evaluate_search(self.evidence.root / 'events.jsonl', self.tasks[-1]['task_id'], '', current_search)
                    if blocked:
                        self.report['blocked'] = True
                        self.report['failures'].append({'stage': self.report['stage'], 'reason': blocked})
                except (OSError, TypeError, ValueError) as evidence_error:
                    self.report['harness_errors'].append({'stage': 'evidence', 'reason': type(evidence_error).__name__})
        finally:
            self.finalize()
        return self.report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case_id')
    parser.add_argument('--work', default='work')
    args = parser.parse_args()
    case = load_case(args.case_id)
    evidence = Evidence(Path('results') / case['id'], configured_secrets())
    def timeout(signum, _frame):
        raise Failure('case_timeout' if signum == signal.SIGALRM else 'runner_canceled')
    signal.signal(signal.SIGALRM, timeout)
    signal.signal(signal.SIGTERM, timeout)
    signal.signal(signal.SIGINT, timeout)
    signal.alarm(case['timeout_seconds'])
    try:
        client = Client(os.environ['MOI_BASE_URL'], os.environ['MOI_PAT'], timeout=case['timeout_seconds'],
                        observer=evidence.observe_transport)
        execution = FunctionalExecution(client, case, evidence, args.work, case['model'])
        report = execution.run()
    except (KeyError, Failure) as error:
        evidence.write('result.json', {'case_id': case['id'], 'workspace': case['workspace'], 'title': case['title'],
            'status': 'error', 'blocked': True, 'failures': [], 'tasks': [],
            'harness_errors': [{'stage': 'configuration', 'reason': type(error).__name__}]})
        return 1
    print(json.dumps({'case_id': case['id'], 'status': report['status'], 'task_seconds': report['task_seconds'],
                      'task_count': len(report['tasks']), 'harness_errors': len(report['harness_errors'])}))
    return 1 if report['harness_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
