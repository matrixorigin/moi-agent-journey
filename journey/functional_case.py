"""Run short, real Agent conversations with deterministic checks and QA evidence."""
import argparse
import json
import os
import re
import signal
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .a2a import final_reply
from .agent_builder import check_candidate_cancel
from .artifact_checks import check_text_artifact, check_xlsx_artifact
from .builder_flow import AgentBuilderFlow, ProductCaseFailure
from .catalog import fixture_path, load_case
from .client import Client, Failure
from .evidence import Evidence, configured_secrets
from .hello import discover
from .run_case import Execution
from .search_checks import evaluate_search
from .skill_checks import check_skill_call
from .sql_checks import evaluate_sql
from .version import finish_version, start_version


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
        if kind not in {'contains', 'not_contains', 'single_number'} or not isinstance(assertion.get('value'), str):
            raise Failure('invalid_case_assertion')
        expected = assertion['value'].format_map(variables)
        if not expected:
            raise Failure('empty_expected_value')
        if kind == 'single_number':
            try:
                target = Decimal(expected)
                numbers = re.findall(r'(?<![\d.])[+-]?\d+(?:\.\d+)?(?![\d.])', reply)
                passed = target.is_finite() and len(numbers) == 1 and Decimal(numbers[0]) == target
            except InvalidOperation:
                raise Failure('invalid_expected_number') from None
            detail = 'numeric_result_mismatch'
        else:
            matched = expected in reply
            passed = matched if kind == 'contains' else not matched
            detail = 'missing_expected_value' if kind == 'contains' else 'unexpected_value'
        checks.append({'session': session_id, 'rule': assertion['rule'], 'passed': passed,
                       'detail': '' if passed else detail})
    return checks


def resource_changes(action):
    changes = action.get('resource_changes')
    if not isinstance(changes, dict) or not changes or not set(changes).issubset({'skills', 'tools'}):
        raise Failure('invalid_resource_changes')
    for operations in changes.values():
        if not isinstance(operations, dict) or not operations or not set(operations).issubset({'add', 'remove'}):
            raise Failure('invalid_resource_changes')
        all_ids = []
        for values in operations.values():
            if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
                raise Failure('invalid_resource_changes')
            all_ids.extend(values)
        if len(all_ids) != len(set(all_ids)):
            raise Failure('invalid_resource_changes')
    return changes


class FunctionalExecution(Execution):
    def run(self):
        current_search = None
        current_search_tool = None
        builders = {}
        try:
            self.root, self.selector = discover(self.client, self.case['workspace'])
            start_version(self)
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
                agent_from = session.get('agent_from')
                selector = None
                if agent_from is not None:
                    builder = builders.get(agent_from)
                    if not builder or not builder.created or not builder.created['verified']:
                        raise ProductCaseFailure('created_agent_unavailable')
                    selector = {'agent_id': builder.created['agent_id'],
                                'agent_workspace_id': builder.created['workspace_id']}
                cid = self.conversation(session['id'], selector=selector)
                final_entry = None
                candidate_verified = False
                artifact_delivered = False
                session_report = {'id': session['id'], 'conversation_id': cid, 'answer_delivered': False,
                                  'candidate_verified': False, 'artifact_delivered': False,
                                  'cancellation_confirmed': False}
                self.report['sessions'].append(session_report)
                builder_types = {'capture_builder_candidate', 'create_builder_agent', 'commit_builder_with_resource',
                                 'revise_builder_and_commit', 'revise_published_and_commit',
                                 'revise_published_resources_and_commit'}
                if any((turn.get('action') or {}).get('type') in builder_types for turn in session['turns']):
                    names = [variables[key] for key in self.case.get('builder_names', [])]
                    if not names:
                        raise Failure('builder_names_missing')
                    builders[session['id']] = AgentBuilderFlow(self, session['id'], checks, names)
                phases = set()
                for turn in session['turns']:
                    if (not isinstance(turn.get('phase'), str) or
                            not re.fullmatch(r'[a-z][a-z0-9_-]*', turn['phase']) or turn['phase'] in phases):
                        raise Failure('invalid_turn_phase')
                    phases.add(turn['phase'])
                    current_search = session['id'] if turn.get('search_evidence') is True else None
                    current_search_tool = turn.get('search_tool') if current_search else None
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
                    cancel = turn.get('cancel_after_admission') is True
                    if cancel and (turn.get('expected_state') != 'canceled' or turn.get('assertions') or turn.get('action')):
                        raise Failure('invalid_cancellation_case')
                    final_entry, task = self.send(cid, session['id'] + ':' + turn['phase'], prompt, attachments,
                                                  cancel_after_admission=cancel)
                    if cancel:
                        accepted = final_entry.get('cancel_request') == 'accepted'
                        canceled = task['status']['state'] == 'canceled'
                        session_report.update({'final_task_id': final_entry['task_id'],
                                               'cancellation_confirmed': accepted and canceled})
                        checks.extend([
                            {'session': session['id'], 'rule': '运行中取消请求被接受', 'passed': accepted,
                             'detail': '' if accepted else 'cancel_request:' + str(final_entry.get('cancel_request'))},
                            {'session': session['id'], 'rule': '任务最终状态为已取消', 'passed': canceled,
                             'detail': '' if canceled else 'task_state:' + str(task['status']['state'])},
                        ])
                        if task['status']['state'] == 'completed':
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'],
                                                            'reason': 'task_finished_before_cancel_observed'})
                        self.report['validation'] = {'checks': checks}
                        self.checkpoint()
                        continue
                    reply = final_reply(task)
                    session_report.update({'final_task_id': final_entry['task_id'], 'answer_delivered': bool(reply.strip())})
                    self.evidence.write('sessions/' + session['id'] + '/replies/' + turn['phase'] + '.json',
                                        {'task_id': final_entry['task_id'], 'text': reply})
                    checks.extend(check_reply(session['id'], turn, reply, variables))
                    if current_search:
                        search_checks, blocked = evaluate_search(self.evidence.root / 'events.jsonl',
                                                                  final_entry['task_id'], reply, session['id'],
                                                                  expected_tool=current_search_tool)
                        checks.extend(search_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('sql_evidence'):
                        sql_checks, blocked = evaluate_sql(self.evidence.root / 'events.jsonl', final_entry['task_id'],
                            session['id'], turn['sql_evidence']['query'], turn['sql_evidence']['column'],
                            turn['sql_evidence']['value'], self.root.rsplit('/', 1)[-1])
                        checks.extend(sql_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    self.report['validation'] = {'checks': checks}
                    self.checkpoint()
                    action = turn.get('action')
                    if action:
                        if action.get('type') == 'reject_invalid_digest_and_cancel':
                            name = action['agent_name'].format_map(variables)
                            candidate_verified = check_candidate_cancel(self, task, final_entry['task_id'],
                                                                         name, checks, session['id'])
                        elif action.get('type') == 'deliver_text_artifact':
                            filename = action['filename'].format_map(variables)
                            content = action['content'].format_map(variables)
                            artifact_delivered = check_text_artifact(self, cid, final_entry['task_id'],
                                                                     filename, content, checks, session['id'])
                        elif action.get('type') in builder_types:
                            builder = builders[session['id']]
                            name = action['agent_name'].format_map(variables)
                            try:
                                if action['type'] == 'revise_builder_and_commit':
                                    builder.revise(task, name)
                                    builder.commit()
                                elif action['type'] == 'revise_published_and_commit':
                                    prior_version = builder.created['version'] if builder.created else None
                                    builder.revise_published(task, name)
                                    revised = builder.commit(expected_description=action['description'].format_map(variables))
                                    builder.record('修订发布了新版本', revised['version'] != prior_version,
                                                   'published_version_not_advanced')
                                elif action['type'] == 'revise_published_resources_and_commit':
                                    changes = resource_changes(action)
                                    prior_version = builder.created['version'] if builder.created else None
                                    builder.revise_published(task, name, changes)
                                    revised = builder.commit(changes=changes)
                                    builder.record('修订发布了新版本', revised['version'] != prior_version,
                                                   'published_version_not_advanced')
                                else:
                                    builder.remember(task, name)
                                    if action['type'] == 'commit_builder_with_resource':
                                        resource = builder.require_resource(action['resource_kind'], action['resource_id'])
                                        builder.commit(resource, capture_current=action.get('capture_current') is True)
                                    elif action['type'] == 'create_builder_agent':
                                        builder.commit()
                            finally:
                                if builder.created:
                                    session_report['created_agent'] = builder.created
                        elif action.get('type') == 'deliver_xlsx_with_skill':
                            check, blocked = check_skill_call(self.evidence.root / 'events.jsonl', final_entry['task_id'],
                                                              action['skill_id'], session['id'])
                            checks.append(check)
                            if blocked:
                                self.report['blocked'] = True
                                self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'],
                                                                'reason': blocked})
                            artifact_delivered = check_xlsx_artifact(self, cid, final_entry['task_id'],
                                action['filename'].format_map(variables), action['content'].format_map(variables),
                                checks, session['id'])
                        else:
                            raise Failure('invalid_case_action')
                    session_report.update({'candidate_verified': candidate_verified, 'artifact_delivered': artifact_delivered})
                if final_entry is None:
                    raise Failure('session_has_no_turns')
                self.checkpoint()
            if not checks:
                raise Failure('quality_checks_missing')
            if all(check['passed'] for check in checks):
                self.report['status'] = 'passed'
            else:
                self.report['status'] = 'failed'
                self.report['failures'].append({'stage': 'validation', 'reason': 'acceptance_checks_failed'})
            self.report['stage'] = 'complete'
        except ProductCaseFailure as error:
            self.report['status'] = 'failed'
            self.report['failures'].append({'stage': self.report['stage'], 'reason': 'acceptance_checks_failed',
                                            'details': str(error)})
        except Exception as error:
            self.record_failure(error)
            if current_search and self.tasks and (self.evidence.root / 'events.jsonl').exists():
                try:
                    _, blocked = evaluate_search(self.evidence.root / 'events.jsonl', self.tasks[-1]['task_id'], '',
                                                  current_search, expected_tool=current_search_tool)
                    if blocked:
                        self.report['blocked'] = True
                        self.report['failures'].append({'stage': self.report['stage'], 'reason': blocked})
                except (OSError, TypeError, ValueError) as evidence_error:
                    self.report['harness_errors'].append({'stage': 'evidence', 'reason': type(evidence_error).__name__})
        finally:
            signal.alarm(0)
            finish_version(self)
            self.finalize()
            cleanup_started = time.monotonic()
            for builder in builders.values():
                builder.cleanup()
            self.report['cleanup_seconds'] += round(time.monotonic() - cleanup_started, 3)
            self.checkpoint()
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
