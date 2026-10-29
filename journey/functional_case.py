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
from .artifact_checks import check_ooxml_artifact, check_text_artifact, check_xlsx_artifact
from .artifact_lifecycle import check_artifact_delete
from .builder_flow import AgentBuilderFlow, ProductCaseFailure
from .catalog import fixture_path, load_case
from .client import Client, Failure
from .conversation_checks import check_history_and_invalid_update, check_lifecycle
from .database_fixture import DatabaseFixture, DatabaseFixtureUnavailable
from .evidence import Evidence, configured_secrets
from .feedback_checks import check_feedback
from .hello import TERMINAL, discover
from .knowledge_checks import evaluate_knowledge, evaluate_knowledge_table
from .knowledge_fixture import KnowledgeFixture, KnowledgeFixtureUnavailable
from .live_resume import check_live_resume
from .message_controls import check_completed_cancellation, check_empty_message
from .run_case import Execution
from .regeneration import regenerate
from .resume_checks import check_resubscribe
from .sandbox_checks import evaluate_python_hash, evaluate_python_recovery
from .search_checks import evaluate_search
from .skill_checks import check_skill_call
from .sql_checks import evaluate_sql, evaluate_sql_failure
from .version import finish_version, start_version
from .web_open_checks import evaluate_open


def case_variables(case):
    names = case.get('random_tokens', [])
    if (not isinstance(names, list) or len(set(names)) != len(names) or
            any(not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name) for name in names)):
        raise Failure('invalid_case_variables')
    return {name: 'J2_' + uuid.uuid4().hex[:16].upper() for name in names}


def json_answer(reply, json_format):
    if json_format == 'object':
        return json.loads(reply)
    decoder = json.JSONDecoder()
    fences = list(re.finditer(r'(?m)^```[^\n]*$', reply))
    if fences:
        blocks = list(re.finditer(r'(?ims)^```(?:json)?[ \t]*\r?\n(.*?)\r?\n```[ \t]*$', reply))
        if len(fences) != 2 or len(blocks) != 1:
            raise ValueError('ambiguous_json_answer')
        block = blocks[0]
        parsed = json.loads(block.group(1))
        surrounding = reply[:block.start()] + reply[block.end():]
    else:
        text = reply.lstrip()
        parsed, end = decoder.raw_decode(text)
        surrounding = text[end:]
    for match in re.finditer(r'\{', surrounding):
        try:
            other, _ = decoder.raw_decode(surrounding[match.start():])
        except ValueError:
            continue
        if isinstance(other, dict):
            raise ValueError('ambiguous_json_answer')
    return parsed


def check_reply(session_id, turn, reply, variables):
    checks = [{'session': session_id, 'rule': turn['phase'] + ':有最终回复',
               'passed': bool(reply.strip()), 'detail': '' if reply.strip() else 'empty_final_reply'}]
    for assertion in turn.get('assertions', []):
        kind = assertion['type']
        if kind not in {'contains', 'not_contains', 'single_number', 'number_present'} or not isinstance(assertion.get('value'), str):
            raise Failure('invalid_case_assertion')
        expected = assertion['value'].format_map(variables)
        if not expected:
            raise Failure('empty_expected_value')
        if kind in {'single_number', 'number_present'}:
            try:
                target = Decimal(expected)
                numbers = re.findall(r'(?<![\d.])[+-]?\d+(?:\.\d+)?(?![\d.])', reply)
                matches = [number for number in numbers if Decimal(number) == target]
                passed = target.is_finite() and bool(matches) and (kind != 'single_number' or len(numbers) == 1)
            except InvalidOperation:
                raise Failure('invalid_expected_number') from None
            detail = 'numeric_result_mismatch'
        else:
            matched = expected in reply
            passed = matched if kind == 'contains' else not matched
            detail = 'missing_expected_value' if kind == 'contains' else 'unexpected_value'
        checks.append({'session': session_id, 'rule': assertion['rule'], 'passed': passed,
                       'detail': '' if passed else detail})
    expected_json = turn.get('json_fields')
    if expected_json is not None:
        if (not isinstance(expected_json, dict) or not expected_json or
                any(not isinstance(key, str) or not key or type(value) not in {str, int, bool, type(None)}
                    for key, value in expected_json.items())):
            raise Failure('invalid_json_fields')
        json_format = turn.get('json_format', 'object')
        if json_format not in {'object', 'object_or_fenced'}:
            raise Failure('invalid_json_format')
        try:
            parsed = json_answer(reply, json_format)
        except (ValueError, TypeError):
            parsed = None
        valid = isinstance(parsed, dict)
        checks.append({'session': session_id, 'rule': '答复是有效的 JSON 对象', 'passed': valid,
                       'detail': '' if valid else 'invalid_json_object'})
        for key, value in expected_json.items():
            expected = value.format_map(variables) if isinstance(value, str) else value
            actual = parsed.get(key) if valid else None
            passed = valid and key in parsed and type(actual) is type(expected) and actual == expected
            checks.append({'session': session_id, 'rule': 'JSON 字段 ' + key + ' 类型和值正确',
                           'passed': passed, 'detail': '' if passed else 'json_field_mismatch:' + key})
    return checks


def check_distinct_attachment_ids(session_id, names, attachments):
    if (not isinstance(names, list) or len(names) < 2 or len(names) != len(attachments) or
            any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names)):
        raise Failure('invalid_distinct_attachments')
    ids = [item.get('file_id') if isinstance(item, dict) else None for item in attachments]
    valid = all(isinstance(file_id, str) and file_id for file_id in ids)
    distinct = valid and len(set(ids)) == len(ids)
    return {'session': session_id, 'rule': '不同附件具有独立的文件 ID', 'passed': distinct,
            'detail': '' if distinct else 'attachment_ids_missing_or_duplicate'}


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
    def check_skill(self, skill_id, task_id, session_id, phase, checks):
        if not isinstance(skill_id, str) or not skill_id:
            raise Failure('invalid_skill_evidence')
        check, blocked = check_skill_call(self.evidence.root / 'events.jsonl', task_id, skill_id, session_id)
        checks.append(check)
        if blocked:
            self.report['blocked'] = True
            self.report['failures'].append({'stage': session_id + ':' + phase, 'reason': blocked})

    def run(self):
        current_search = None
        current_search_tool = None
        current_open = None
        builders = {}
        knowledge = None
        database = None
        try:
            self.root, self.selector = discover(self.client, self.case['workspace'])
            start_version(self)
            variables = case_variables(self.case)
            if self.case.get('knowledge_fixture') is not None:
                self.report['stage'] = 'knowledge_fixture'
                knowledge = KnowledgeFixture(self, self.case['knowledge_fixture'])
                variables.update(knowledge.setup())
            if self.case.get('database_fixture') is not None:
                self.report['stage'] = 'database_fixture'
                database = DatabaseFixture(self, self.case['database_fixture'])
                variables.update(database.setup())
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
                                 'commit_builder_with_resources',
                                 'revise_builder_and_commit', 'revise_published_and_commit',
                                 'revise_published_resources_and_commit', 'revise_published_instruction_and_commit'}
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
                    current_open = (session['id'], turn['open_evidence']) if turn.get('open_evidence') else None
                    attachments = []
                    for name in turn.get('attachments', []):
                        if name not in fixtures:
                            raise Failure('unknown_fixture')
                        if name not in uploaded:
                            try:
                                upload = self.client.upload(self.root, fixture_path(self.case, fixtures[name]))
                            except Failure as error:
                                if (error.code not in {'http_error', 'upload_product_error'} or
                                        (error.code == 'http_error' and
                                         (type(error.http_status) is not int or error.http_status in {401, 403}))):
                                    raise
                                detail = (error.code + ':' + str(error.http_status) if error.http_status is not None
                                          else error.code)
                                checks.append({'session': session['id'], 'rule': '附件上传成功', 'passed': False,
                                               'detail': detail})
                                self.report['validation'] = {'checks': checks}
                                self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'],
                                    'reason': error.code, 'http_status': error.http_status, 'details': error.details})
                                if error.http_status in {408, 429, 503}:
                                    self.report['blocked'] = True
                                self.checkpoint()
                                raise ProductCaseFailure('attachment_upload_failed') from None
                            self.uploads.append(upload)
                            self.evidence.write('uploads.json', self.uploads)
                            if any(not isinstance(upload.get(key), str) or not upload[key]
                                   for key in ('file_id', 'original_name')):
                                checks.append({'session': session['id'], 'rule': '附件上传返回有效文件信息',
                                               'passed': False, 'detail': 'invalid_upload_metadata'})
                                self.report['validation'] = {'checks': checks}
                                self.checkpoint()
                                raise ProductCaseFailure('invalid_upload_metadata')
                            self.report.setdefault('retained_inputs', []).append({
                                'fixture': name, 'file_id': upload['file_id'], 'reason': 'conversation_attachment'})
                            uploaded[name] = upload
                        attachments.append(uploaded[name])
                    distinct_ids = turn.get('distinct_attachment_ids', False)
                    if type(distinct_ids) is not bool:
                        raise Failure('invalid_distinct_attachments')
                    if distinct_ids:
                        checks.append(check_distinct_attachment_ids(session['id'], turn.get('attachments', []), attachments))
                        self.report['validation'] = {'checks': checks}
                        self.checkpoint()
                    prompt = turn['prompt'].format_map(variables)
                    cancel = turn.get('cancel_after_admission') is True
                    live_resume = turn.get('live_resume', False)
                    if type(live_resume) is not bool or (cancel and live_resume):
                        raise Failure('invalid_live_resume_case')
                    if cancel and (turn.get('expected_state') != 'canceled' or turn.get('assertions') or turn.get('action')):
                        raise Failure('invalid_cancellation_case')
                    regeneration = turn.get('regenerate', False)
                    if type(regeneration) is not bool:
                        raise Failure('invalid_regeneration_case')
                    if regeneration:
                        if final_entry is None or attachments or cancel or live_resume or prompt:
                            raise Failure('invalid_regeneration_case')
                        final_entry, task = regenerate(self, cid, session['id'],
                                                      session['id'] + ':' + turn['phase'], final_entry, checks)
                    else:
                        final_entry, task = self.send(cid, session['id'] + ':' + turn['phase'], prompt, attachments,
                                                      cancel_after_admission=cancel, live_resume=live_resume)
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
                    if live_resume:
                        blocked = check_live_resume(self, final_entry, checks, session['id'])
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    required_skill = turn.get('skill_evidence')
                    if required_skill is not None:
                        self.check_skill(required_skill, final_entry['task_id'], session['id'], turn['phase'], checks)
                    if current_search:
                        search_checks, blocked = evaluate_search(self.evidence.root / 'events.jsonl',
                                                                  final_entry['task_id'], reply, session['id'],
                                                                  expected_tool=current_search_tool)
                        checks.extend(search_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if current_open:
                        open_checks, blocked = evaluate_open(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], current_open[1]['url'], current_open[1]['content'])
                        checks.extend(open_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('sandbox_hash'):
                        hash_checks, blocked = evaluate_python_hash(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], variables[turn['sandbox_hash']], reply)
                        checks.extend(hash_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('sandbox_recovery'):
                        config = turn['sandbox_recovery']
                        recovery_checks, blocked = evaluate_python_recovery(
                            self.evidence.root / 'events.jsonl', final_entry['task_id'], session['id'],
                            variables[config['marker']], config['left'], config['right'],
                            require_failed_tool=config.get('require_failed_tool', False))
                        checks.extend(recovery_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('sql_evidence'):
                        sql_checks, blocked = evaluate_sql(self.evidence.root / 'events.jsonl', final_entry['task_id'],
                            session['id'], turn['sql_evidence']['query'].format_map(variables), turn['sql_evidence']['column'],
                            turn['sql_evidence']['value'], self.root.rsplit('/', 1)[-1])
                        checks.extend(sql_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('sql_error_evidence'):
                        config = turn['sql_error_evidence']
                        failure_checks, blocked = evaluate_sql_failure(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], config['query'].format_map(variables),
                            config['error_contains'], self.root.rsplit('/', 1)[-1])
                        checks.extend(failure_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('knowledge_evidence') is True:
                        if not knowledge or knowledge.kind != 'document' or knowledge.state.get('status') != 'ready':
                            raise Failure('knowledge_fixture_not_ready')
                        knowledge_checks, blocked = evaluate_knowledge(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], knowledge.state, knowledge.marker)
                        checks.extend(knowledge_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('knowledge_table_evidence') is True:
                        if not knowledge or knowledge.kind != 'table' or knowledge.state.get('status') != 'ready':
                            raise Failure('knowledge_fixture_not_ready')
                        table_checks, blocked = evaluate_knowledge_table(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], knowledge.state, knowledge.verification)
                        checks.extend(table_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    self.report['validation'] = {'checks': checks}
                    self.checkpoint()
                    action = turn.get('action')
                    if action:
                        if action.get('type') == 'reject_empty_message':
                            check_empty_message(self, cid, session['id'], checks)
                        elif action.get('type') == 'cancel_completed':
                            check_completed_cancellation(self, cid, session['id'], task, checks)
                        elif action.get('type') == 'feedback':
                            check_feedback(self, cid, session['id'], task, checks)
                        elif action.get('type') == 'conversation_lifecycle':
                            check_lifecycle(self, cid, session['id'], checks, action['title'].format_map(variables))
                        elif action.get('type') == 'conversation_history':
                            check_history_and_invalid_update(self, cid, session['id'], checks,
                                action['title'].format_map(variables),
                                [item['prompt'].format_map(variables) for item in session['turns']
                                 if item['phase'] in phases])
                        elif action.get('type') == 'reject_invalid_digest_and_cancel':
                            name = action['agent_name'].format_map(variables)
                            candidate_verified = check_candidate_cancel(self, task, final_entry['task_id'],
                                                                         name, checks, session['id'])
                        elif action.get('type') == 'deliver_text_artifact':
                            filename = action['filename'].format_map(variables)
                            content = action['content'].format_map(variables)
                            artifact_delivered = check_text_artifact(self, cid, final_entry['task_id'],
                                                                     filename, content, checks, session['id'])
                        elif action.get('type') == 'delete_text_artifact':
                            filename = action['filename'].format_map(variables)
                            content = action['content'].format_map(variables)
                            artifact_delivered = check_text_artifact(self, cid, final_entry['task_id'],
                                                                     filename, content, checks, session['id'])
                            session_report['artifact_delivered'] = artifact_delivered
                            self.checkpoint()
                            if not artifact_delivered or not checks[-1]['passed']:
                                raise ProductCaseFailure('artifact_delivery_not_verified')
                            check_artifact_delete(self, cid, final_entry['task_id'], filename, checks, session['id'])
                        elif action.get('type') == 'check_resubscribe':
                            check_resubscribe(self, final_entry, checks, session['id'])
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
                                elif action['type'] == 'revise_published_instruction_and_commit':
                                    prior_version = builder.created['version'] if builder.created else None
                                    builder.revise_published(task, name)
                                    revised = builder.commit(expected_instruction=action['instruction_marker'].format_map(variables))
                                    builder.record('修订发布了新版本', revised['version'] != prior_version,
                                                   'published_version_not_advanced')
                                else:
                                    builder.remember(task, name)
                                    if action['type'] == 'commit_builder_with_resource':
                                        resource = builder.require_resource(action['resource_kind'],
                                                                            action['resource_id'].format_map(variables))
                                        builder.commit([resource], capture_current=action.get('capture_current') is True)
                                    elif action['type'] == 'commit_builder_with_resources':
                                        requested = action.get('resources')
                                        if (not isinstance(requested, list) or not requested or
                                                any(not isinstance(item, dict) or set(item) != {'kind', 'id'} or
                                                    item['kind'] not in {'skills', 'tools', 'knowledge_bases'} or
                                                    not isinstance(item['id'], str) or not item['id'] for item in requested) or
                                                len({(item['kind'], item['id']) for item in requested}) != len(requested)):
                                            raise Failure('invalid_builder_resources')
                                        resources = [builder.require_resource(item['kind'], item['id'].format_map(variables))
                                                     for item in requested]
                                        builder.commit(resources, capture_current=action.get('capture_current') is True)
                                    elif action['type'] == 'create_builder_agent':
                                        builder.commit()
                            finally:
                                if builder.created:
                                    session_report['created_agent'] = builder.created
                        elif action.get('type') in {'deliver_xlsx_with_skill', 'deliver_ooxml_with_skill'}:
                            self.check_skill(action['skill_id'], final_entry['task_id'], session['id'], turn['phase'], checks)
                            filename, content = action['filename'].format_map(variables), action['content'].format_map(variables)
                            if action['type'] == 'deliver_xlsx_with_skill':
                                artifact_delivered = check_xlsx_artifact(self, cid, final_entry['task_id'],
                                    filename, content, checks, session['id'])
                            else:
                                artifact_delivered = check_ooxml_artifact(self, cid, final_entry['task_id'],
                                    filename, content, action['kind'], checks, session['id'])
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
        except KnowledgeFixtureUnavailable as error:
            self.report['status'] = 'failed'
            self.report['blocked'] = True
            self.report['failures'].append({'stage': 'knowledge_fixture', 'reason': str(error)})
        except DatabaseFixtureUnavailable as error:
            self.report['status'] = 'failed'
            self.report['blocked'] = True
            self.report['failures'].append({'stage': 'database_fixture', 'reason': str(error)})
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
            if current_open and self.tasks and (self.evidence.root / 'events.jsonl').exists():
                try:
                    _, blocked = evaluate_open(self.evidence.root / 'events.jsonl', self.tasks[-1]['task_id'],
                                                current_open[0], current_open[1]['url'], current_open[1]['content'])
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
            tasks_confirmed = (self.pending_conversation is None and
                               all(task.get('state') in TERMINAL for task in self.tasks))
            if knowledge:
                dependencies_confirmed = (tasks_confirmed and not any(
                    item.get('stage') == 'cleanup_agent' for item in self.report['harness_errors']))
                knowledge.cleanup(dependencies_confirmed=dependencies_confirmed)
            if database:
                database.cleanup(dependencies_confirmed=tasks_confirmed)
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
