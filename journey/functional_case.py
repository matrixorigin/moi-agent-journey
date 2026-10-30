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
from .a2ui_checks import evaluate_a2ui, question_action, form_action
from .agent_builder import check_candidate_cancel
from .artifact_checks import (check_ooxml_artifact, check_text_artifact, check_xlsx_artifact,
                              check_text_artifacts, check_pdf_artifact)
from .artifact_lifecycle import check_artifact_delete
from .builder_flow import AgentBuilderFlow, ProductCaseFailure
from .catalog import fixture_path, load_case
from .citation_checks import check_citation_open
from .client import Client, Failure
from .conversation_checks import (check_history_and_invalid_update, check_lifecycle,
                                  check_agent_scope, check_archived_delivery)
from .database_fixture import DatabaseFixture, DatabaseFixtureUnavailable
from .evidence import Evidence, configured_secrets
from .feedback_checks import check_feedback
from .file_read_checks import evaluate_file_rejection
from .hello import TERMINAL, discover
from .knowledge_checks import evaluate_knowledge, evaluate_knowledge_table
from .knowledge_fixture import KnowledgeFixtureUnavailable, knowledge_selection_ids, prepare_knowledge_fixtures
from .live_resume import check_live_resume
from .pagination_checks import check_pagination
from .message_controls import check_completed_cancellation, check_empty_message, check_invalid_message
from .run_case import Execution
from .regeneration import regenerate
from .resume_checks import check_resubscribe
from .sandbox_checks import evaluate_file_state, evaluate_python_hash, evaluate_python_recovery
from .search_checks import evaluate_search
from .skill_checks import check_skill_call
from .skill_journey import SkillJourney
from .native_skill_journey import NativeSkillJourney
from .native_skill_builder import NativeSkillBuilderJourney
from .native_skill_source import NativeSkillUnavailable
from .tool_journey import ToolJourney, evaluate_http_tool
from .resource_lifecycle import ResourceLifecycleJourney
from .builder_input_journey import BuilderInputJourney
from .builder_edge_journey import BuilderEdgeJourney
from .agent_package_journey import AgentPackageJourney
from .task_failure import expected_model_failure
from .builder_recovery import BuilderRecoveryJourney
from .artifact_knowledge import ArtifactKnowledgeFixture
from .embedded_skill import EmbeddedSkillJourney
from .system_skill_journey import SystemSkillJourney
from .channel_journey import ChannelJourney, ChannelUnavailable, channel_targets, evaluate_channel_call, resolve_values
from .channel_receivers import verify_receiver
from .tool_failure_checks import evaluate_tool_failure
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


def resource_changes(action, variables=None, knowledge=None):
    changes = action.get('resource_changes')
    if not isinstance(changes, dict) or not changes or not set(changes).issubset({'skills', 'tools', 'knowledge_bases'}):
        raise Failure('invalid_resource_changes')
    resolved = {}
    for kind, operations in changes.items():
        if not isinstance(operations, dict) or not operations or not set(operations).issubset({'add', 'remove'}):
            raise Failure('invalid_resource_changes')
        all_ids, resolved[kind] = [], {}
        for operation, values in operations.items():
            if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
                raise Failure('invalid_resource_changes')
            try:
                values = [value.format_map(variables or {}) for value in values]
            except (KeyError, ValueError):
                raise Failure('invalid_resource_changes') from None
            resolved[kind][operation] = values
            all_ids.extend(values)
        if len(all_ids) != len(set(all_ids)):
            raise Failure('invalid_resource_changes')
        if kind == 'knowledge_bases':
            owned = {str(item.state['model_id']) for item in (knowledge or {}).values()
                     if item.state.get('status') == 'ready' and type(item.state.get('model_id')) is int}
            if not set(all_ids).issubset(owned):
                raise Failure('invalid_resource_changes')
    return resolved


class FunctionalExecution(Execution):
    def cleanup(self):
        native = getattr(self, "native_resources", None)
        generated = getattr(self, 'generated_knowledge', None)
        def cleanup_outputs(cid):
            if generated and generated.state.get('conversation_id') == cid:
                confirmed = self.pending_conversation is None and all(task.get('state') in TERMINAL for task in self.tasks)
                generated.cleanup(dependencies_confirmed=confirmed)
            if native:
                native.cleanup_outputs(cid)
        super().cleanup(cleanup_outputs=cleanup_outputs if native or generated else None)

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
        knowledge = {}
        database = None
        skill_journey = None
        self.native_skill = None
        self.native_builder = None
        self.native_resources = None
        tool_journey = None
        resource_lifecycle = None
        builder_input = None
        package_journey = None
        channel_journeys = {}
        checks = []
        try:
            self.root, self.selector = discover(self.client, self.case['workspace'])
            start_version(self)
            variables = case_variables(self.case)
            if self.case.get('resource_lifecycle') is not None:
                self.report['stage'] = 'resource_lifecycle'
                resource_lifecycle = ResourceLifecycleJourney(self, self.case['resource_lifecycle'], checks)
                variables.update(resource_lifecycle.prepare())
            if self.case.get('channels') is not None:
                self.report['stage'] = 'channel_preflight'
                targets = channel_targets(self, self.case['channels'], checks)
                for name, target in targets.items():
                    channel = ChannelJourney(self, name, target, checks)
                    channel_journeys[name] = channel
                    variables.update(channel.prepare())
            if self.case.get('builder_edge') is not None:
                self.report['stage'] = 'builder_edge'
                builder_input = BuilderEdgeJourney(self, self.case['builder_edge'], checks)
                builder_input.prepare()
            if self.case.get('builder_recovery') is not None:
                self.report['stage'] = 'builder_recovery'
                builder_input = BuilderRecoveryJourney(self, self.case['builder_recovery'], checks)
                builder_input.prepare()
            if self.case.get('system_skills') is not None:
                self.report['stage'] = 'system_skills'
                builder_input = SystemSkillJourney(self, self.case['system_skills'], checks)
                builder_input.prepare()
            if self.case.get('package_journey') is not None:
                self.report['stage'] = 'package_journey'
                package_type = (EmbeddedSkillJourney if self.case['package_journey'].get('mode') == 'embedded_skill'
                                else AgentPackageJourney)
                package_journey = package_type(self, self.case['package_journey'], checks)
                variables.update(package_journey.prepare())
            if self.case.get('builder_input_journey') is True:
                self.report['stage'] = 'builder_input_journey'
                builder_input = BuilderInputJourney(self, checks)
                builder_input.prepare()
            if self.case.get('native_skill_journey') is not None:
                if self.case.get('skill_journey') is not None or self.case.get('native_skill_builder'):
                    raise Failure('conflicting_skill_journeys')
                self.report['stage'] = 'native_skill_journey'
                skill_journey = self.native_resources = self.native_skill = NativeSkillJourney(self, self.case['native_skill_journey'], checks)
                variables.update(skill_journey.prepare())
            if self.case.get('native_skill_builder') is True:
                if self.case.get('skill_journey') is not None:
                    raise Failure('conflicting_skill_journeys')
                self.report['stage'] = 'native_skill_builder'
                skill_journey = self.native_resources = self.native_builder = NativeSkillBuilderJourney(self, checks)
                variables.update(skill_journey.prepare())
            if self.case.get('skill_journey') is not None:
                self.report['stage'] = 'skill_journey'
                skill_journey = SkillJourney(self, self.case['skill_journey'], variables, checks)
                variables.update(skill_journey.prepare())
            if self.case.get('tool_journey') is not None:
                self.report['stage'] = 'tool_journey'
                tool_journey = ToolJourney(self, self.case['tool_journey'], checks)
                variables.update(tool_journey.prepare())
            prepare_knowledge_fixtures(self, knowledge, variables)
            if self.case.get('database_fixture') is not None:
                self.report['stage'] = 'database_fixture'
                database = DatabaseFixture(self, self.case['database_fixture'])
                variables.update(database.setup())
            self.evidence.write('parameters.json', variables)
            fixtures = {item['path']: item for item in self.case.get('fixtures', [])}
            if len(fixtures) != len(self.case.get('fixtures', [])):
                raise Failure('duplicate_fixture')
            uploaded = {}
            session_ids = set()
            for session in self.case['sessions']:
                if (not isinstance(session.get('id'), str) or
                        not re.fullmatch(r'[a-z][a-z0-9_-]*', session['id']) or session['id'] in session_ids):
                    raise Failure('invalid_session_id')
                session_ids.add(session['id'])
                self.report['stage'] = 'session:' + session['id']
                agent_from = session.get('agent_from')
                selector = None
                selectors = [session.get(key) for key in ('agent_from', 'agent_from_tool', 'agent_from_skill', 'agent_from_builder_input', 'agent_from_package', 'agent_from_channel', 'agent_from_lifecycle')]
                if sum(bool(value) for value in selectors) > 1:
                    raise Failure('conflicting_agent_selectors')
                if session.get('agent_from_lifecycle') is True:
                    if not resource_lifecycle:
                        raise Failure('invalid_lifecycle_session')
                    selector = resource_lifecycle.selector()
                if session.get('agent_from_channel'):
                    channel = channel_journeys.get(session['agent_from_channel'])
                    if not channel:
                        raise Failure('invalid_channel_session')
                    selector = channel.selector()
                if session.get('agent_from_builder_input') is True:
                    if not builder_input:
                        raise Failure('invalid_builder_input_session')
                    selector = builder_input.selector()
                if session.get('agent_from_package') is True:
                    if not package_journey:
                        raise Failure('invalid_package_agent_session')
                    selector = package_journey.selector()
                if session.get('agent_from_tool') is True:
                    if not tool_journey or agent_from is not None or session.get('agent_from_skill'):
                        raise Failure('invalid_tool_agent_session')
                    selector = tool_journey.selector()
                if session.get('agent_from_skill') is True:
                    if not skill_journey or agent_from is not None:
                        raise Failure('invalid_skill_agent_session')
                    selector = skill_journey.selector()
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
                                 'revise_published_resources_and_commit', 'revise_published_instruction_and_commit',
                                 'create_builder_agent_and_check_lifecycle'}
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
                    if self.native_skill and not self.native_skill.admit(turn):
                        continue
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
                    if turn.get('channel_evidence'):
                        operation = resolve_values(turn['channel_evidence'], variables)
                        prompt += '\n工具：' + operation['tool'] + '\n参数：' + json.dumps(operation['arguments'], ensure_ascii=False)
                    action_data, prior_surface_task = None, None
                    if turn.get('a2ui_form_submit') is not None:
                        if final_entry is None or turn.get('a2ui_submit') is not None:
                            raise Failure('invalid_form_submit_case')
                        config = turn['a2ui_form_submit']
                        fields = {key: value.format_map(variables) if isinstance(value, str) else value
                                  for key, value in config['fields'].items()}
                        action_data = form_action(self.evidence.root / 'events.jsonl', final_entry['task_id'], fields)
                        checks.append({'session': session['id'], 'rule': '表单字段通过真实按钮的上下文路径提交',
                            'passed': action_data is not None, 'detail': '' if action_data else 'form_not_submittable'})
                        if action_data is None:
                            raise ProductCaseFailure('form_not_submittable')
                    if turn.get('a2ui_submit') is not None:
                        config = turn['a2ui_submit']
                        if final_entry is None or 'a2ui_evidence' not in turn:
                            raise Failure('invalid_a2ui_submit_case')
                        prior_surface_task = final_entry['task_id']
                        action_data = question_action(self.evidence.root / 'events.jsonl', prior_surface_task,
                                                      config['labels'], config['answer'],
                                                      question_type=config.get('type', 'single'))
                        checks.append({'session': session['id'], 'rule': '从实际问题中读取可提交的选项和组件身份',
                            'passed': action_data is not None, 'detail': '' if action_data else 'a2ui_question_not_submittable'})
                        self.report['validation'] = {'checks': checks}
                        self.checkpoint()
                        if action_data is None:
                            raise ProductCaseFailure('a2ui_question_not_submittable')
                    selection = turn.get('knowledge_selection')
                    knowledge_ids = knowledge_selection_ids(knowledge, selection) if selection is not None else None
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
                        if (final_entry is None or attachments or cancel or live_resume or prompt or knowledge_ids is not None
                                or turn.get('file_only') or action_data is not None):
                            raise Failure('invalid_regeneration_case')
                        final_entry, task = regenerate(self, cid, session['id'],
                                                      session['id'] + ':' + turn['phase'], final_entry, checks)
                    elif turn.get('expected_model_failure') is True:
                        if (turn.get('expected_state') != 'failed' or attachments or cancel or live_resume or
                                action_data is not None or turn.get('assertions') or turn.get('action') or
                                self.native_skill or knowledge_ids is not None):
                            raise Failure('invalid_expected_model_failure')
                        expected_model_failure(self, cid, session['id'] + ':' + turn['phase'], prompt,
                                               session['id'], checks)
                        continue
                    else:
                        sender = self.native_skill.send if self.native_skill else self.send
                        final_entry, task = sender(cid, session['id'] + ':' + turn['phase'], prompt, attachments,
                                                      cancel_after_admission=cancel, live_resume=live_resume,
                                                      knowledge_ids=knowledge_ids, a2ui_action=action_data, file_only=turn.get("file_only", False))
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
                    if 'a2ui_evidence' in turn:
                        if turn.get('assertions') or turn.get('action') or turn.get('json_fields'):
                            raise Failure('ambiguous_a2ui_case')
                        expected_surface = turn['a2ui_evidence']
                        if action_data:
                            expected_surface = {**expected_surface, 'fields': {**expected_surface['fields'],
                                                'id': action_data['action']['sourceComponentId']}}
                        surface_checks, delivered = evaluate_a2ui(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], expected_surface, prior_task_id=prior_surface_task)
                        checks.extend(surface_checks)
                        session_report.update({'final_task_id': final_entry['task_id'], 'answer_delivered': delivered})
                        if action_data is None:
                            self.check_skill('a2ui', final_entry['task_id'], session['id'], turn['phase'], checks)
                        self.report['validation'] = {'checks': checks}
                        self.checkpoint()
                        continue
                    reply = final_reply(task)
                    session_report.update({'final_task_id': final_entry['task_id'], 'answer_delivered': bool(reply.strip())})
                    self.evidence.write('sessions/' + session['id'] + '/replies/' + turn['phase'] + '.json',
                                        {'task_id': final_entry['task_id'], 'text': reply})
                    checks.extend(check_reply(session['id'], turn, reply, variables))
                    if turn.get('artifact_to_knowledge') is not None:
                        spec = turn['artifact_to_knowledge']
                        if (not isinstance(spec, dict) or set(spec) != {'filename', 'marker'} or
                                'generated' in knowledge):
                            raise Failure('invalid_artifact_knowledge')
                        fixture = ArtifactKnowledgeFixture(self, cid, final_entry['task_id'],
                            spec['filename'].format_map(variables), spec['marker'].format_map(variables), checks, session['id'])
                        knowledge['generated'] = fixture
                        self.generated_knowledge = fixture
                        try:
                            values = fixture.setup()
                        except KnowledgeFixtureUnavailable:
                            checks.append({'session': session['id'], 'rule': '生成成果通过解析和卷文件选择进入知识库并解析完成',
                                           'passed': False, 'detail': 'artifact_knowledge_conversion_failed'})
                            raise ProductCaseFailure('artifact_knowledge_conversion_failed') from None
                        variables.update({'generated_' + key: value for key, value in values.items()})
                        checks.append({'session': session['id'], 'rule': '生成成果通过解析和卷文件选择进入知识库并解析完成',
                                       'passed': fixture.state.get('status') == 'ready', 'detail': ''})
                    if turn.get('lifecycle_check') is not None:
                        if not resource_lifecycle:
                            raise Failure('invalid_lifecycle_check')
                        resource_lifecycle.evaluate(final_entry['task_id'], session['id'], turn['lifecycle_check'])
                    if self.native_skill:
                        artifact_delivered |= self.native_skill.evaluate(final_entry['task_id'], cid, session['id'],
                            turn['phase'], reply, turn['native_check'], variables)
                    if self.native_builder:
                        self.native_builder.check_execution(final_entry['task_id'], session['id'] + ':' + turn['phase'])
                    if live_resume:
                        blocked = check_live_resume(self, final_entry, checks, session['id'])
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    required_skill = turn.get('skill_evidence')
                    if required_skill is not None:
                        skill_ids = [required_skill] if isinstance(required_skill, str) else required_skill
                        if (not isinstance(skill_ids, list) or not skill_ids or
                                any(not isinstance(item, str) or not item for item in skill_ids) or
                                len(set(skill_ids)) != len(skill_ids)):
                            raise Failure('invalid_skill_evidence')
                        for skill_id in skill_ids:
                            self.check_skill(skill_id.format_map(variables), final_entry['task_id'], session['id'], turn['phase'], checks)
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
                    if turn.get('channel_evidence'):
                        output = evaluate_channel_call(self, final_entry['task_id'], session['id'],
                            turn['channel_evidence'], variables, checks)
                        if turn.get('receiver'):
                            verify_receiver(self, session['agent_from_channel'], output, variables['code'],
                                turn['receiver'], session['id'], checks)
                    if turn.get('file_rejection') is True:
                        if len(attachments) != 1:
                            raise Failure('invalid_damaged_file_case')
                        checks.extend(evaluate_file_rejection(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], attachments[0]))
                    if turn.get('tool_failure'):
                        config = {k: v.format_map(variables) if isinstance(v, str) else v for k, v in turn['tool_failure'].items()}
                        checks.extend(evaluate_tool_failure(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], config))
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
                    if turn.get('citation_open'):
                        alias = turn['citation_open']
                        if not isinstance(alias, str) or alias not in knowledge:
                            raise Failure('invalid_citation_open_case')
                        check_citation_open(self, cid, final_entry['task_id'], session['id'], checks, knowledge[alias])
                    if turn.get('http_tool_evidence'):
                        config = turn['http_tool_evidence']
                        checks.extend(evaluate_http_tool(self.evidence.root / 'events.jsonl', final_entry['task_id'],
                            session['id'], config['tool_id'].format_map(variables), config['status'], config.get('body_id')))
                    if turn.get('sandbox_file'):
                        config = turn['sandbox_file']
                        file_checks, blocked = evaluate_file_state(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], config['path'].format_map(variables),
                            variables[config['content']], config['mode'])
                        checks.extend(file_checks)
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
                            config['error_contains'], self.root.rsplit('/', 1)[-1], error_kind=config.get('error_kind', 'column'))
                        checks.extend(failure_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    evidence_names = turn.get('knowledge_evidence', False)
                    if evidence_names is True:
                        evidence_names = ['default']
                    if evidence_names is not False:
                        if (not isinstance(evidence_names, list) or not evidence_names or
                                any(not isinstance(alias, str) or alias not in knowledge for alias in evidence_names) or
                                len(set(evidence_names)) != len(evidence_names)):
                            raise Failure('invalid_knowledge_evidence')
                        for alias in evidence_names:
                            fixture = knowledge[alias]
                            if fixture.kind != 'document' or fixture.state.get('status') != 'ready':
                                raise Failure('knowledge_fixture_not_ready')
                            knowledge_checks, blocked = evaluate_knowledge(self.evidence.root / 'events.jsonl',
                                final_entry['task_id'], session['id'], fixture.state, fixture.marker)
                            if len(evidence_names) > 1:
                                for check in knowledge_checks:
                                    check['rule'] += ' (' + alias + ')'
                            checks.extend(knowledge_checks)
                            if blocked:
                                self.report['blocked'] = True
                                self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    if turn.get('knowledge_table_evidence'):
                        alias = 'default' if turn['knowledge_table_evidence'] is True else turn['knowledge_table_evidence']
                        if not isinstance(alias, str):
                            raise Failure('invalid_knowledge_table_evidence')
                        fixture = knowledge.get(alias)
                        if not fixture or fixture.kind != 'table' or fixture.state.get('status') != 'ready':
                            raise Failure('knowledge_fixture_not_ready')
                        table_checks, blocked = evaluate_knowledge_table(self.evidence.root / 'events.jsonl',
                            final_entry['task_id'], session['id'], fixture.state, fixture.verification)
                        checks.extend(table_checks)
                        if blocked:
                            self.report['blocked'] = True
                            self.report['failures'].append({'stage': session['id'] + ':' + turn['phase'], 'reason': blocked})
                    self.report['validation'] = {'checks': checks}
                    self.checkpoint()
                    action = turn.get('action')
                    if action:
                        if action.get('type') == 'enable_custom_skill':
                            if not skill_journey or skill_journey.mode != 'disabled':
                                raise Failure('invalid_skill_enable_action')
                            skill_journey.set_status('active')
                        elif action.get('type') == 'reject_empty_message':
                            check_empty_message(self, cid, session['id'], checks)
                        elif action.get('type') == 'cancel_completed':
                            check_completed_cancellation(self, cid, session['id'], task, checks)
                        elif action.get('type') == 'feedback':
                            check_feedback(self, cid, session['id'], task, checks,
                                rating=action.get('rating', 'up'), comment=action.get('comment'),
                                labels=action.get('labels'), check_filter=action.get('check_filter', False),
                                check_stats=action.get('check_stats', False))
                        elif action.get('type') == 'reject_messages':
                            defects = action.get('defects')
                            if not isinstance(defects, list) or not defects or len(set(defects)) != len(defects):
                                raise Failure('invalid_message_defects')
                            for defect in defects:
                                check_invalid_message(self, cid, session['id'], checks, defect,
                                                      upload=attachments[0] if len(attachments) == 1 else None)
                        elif action.get('type') == 'delete_selected_knowledge':
                            fixture = knowledge.get(action.get('fixture'))
                            if not fixture or fixture.state.get('status') != 'ready':
                                raise Failure('invalid_knowledge_retirement')
                            tasks_confirmed = self.pending_conversation is None and all(item.get('state') in TERMINAL for item in self.tasks)
                            fixture.cleanup(dependencies_confirmed=tasks_confirmed)
                            deleted = fixture.state.get('cleanup') == 'deleted'
                            checks.append({'session': session['id'], 'rule': '本次知识库已删除并确认不可读取',
                                'passed': deleted, 'detail': '' if deleted else 'knowledge_delete_unconfirmed'})
                            if not deleted:
                                raise ProductCaseFailure('knowledge_delete_unconfirmed')
                            check_invalid_message(self, cid, session['id'], checks, 'deleted_knowledge',
                                                  knowledge_id=str(fixture.state['model_id']))
                        elif action.get('type') == 'conversation_lifecycle':
                            check_lifecycle(self, cid, session['id'], checks, action['title'].format_map(variables))
                        elif action.get('type') == 'conversation_agent_scope':
                            others = [item for item in self.report['sessions'] if item['id'] == action['other_session']]
                            if len(others) != 1:
                                raise Failure('invalid_conversation_scope_case')
                            check_agent_scope(self, cid, others[0]['conversation_id'], session['id'], checks)
                        elif action.get('type') == 'conversation_history':
                            check_history_and_invalid_update(self, cid, session['id'], checks,
                                action['title'].format_map(variables),
                                [item['prompt'].format_map(variables) for item in session['turns']
                                 if item['phase'] in phases])
                        elif action.get('type') == 'reject_invalid_digest_and_cancel':
                            name = action['agent_name'].format_map(variables)
                            candidate_verified = check_candidate_cancel(self, task, final_entry['task_id'],
                                name, checks, session['id'], check_commit=action.get('check_canceled_commit') is True)
                        elif action.get('type') == 'deliver_text_artifact':
                            filename = action['filename'].format_map(variables)
                            content = action['content'].format_map(variables)
                            artifact_delivered = check_text_artifact(self, cid, final_entry['task_id'],
                                                                     filename, content, checks, session['id'])
                        elif action.get('type') == 'check_pagination':
                            check_pagination(self, cid, session['id'], checks)
                        elif action.get('type') == 'deliver_text_artifacts':
                            files = [{key: value.format_map(variables) for key, value in item.items()}
                                     for item in action['files']]
                            artifact_delivered = check_text_artifacts(self, cid, final_entry['task_id'],
                                                                      files, checks, session['id'])
                        elif action.get('type') in {'delete_text_artifact', 'archive_text_artifact'}:
                            filename = action['filename'].format_map(variables)
                            content = action['content'].format_map(variables)
                            artifact_delivered = check_text_artifact(self, cid, final_entry['task_id'],
                                                                     filename, content, checks, session['id'])
                            session_report['artifact_delivered'] = artifact_delivered
                            self.checkpoint()
                            if not artifact_delivered or not checks[-1]['passed']:
                                raise ProductCaseFailure('artifact_delivery_not_verified')
                            if action['type'] == 'archive_text_artifact':
                                check_archived_delivery(self, cid, final_entry['task_id'], session['id'], checks, filename, content,
                                                        archive_agent=action.get('archive_agent', False))
                            else:
                                check_artifact_delete(self, cid, final_entry['task_id'], filename, checks, session['id'])
                        elif action.get('type') == 'check_resubscribe':
                            check_resubscribe(self, final_entry, checks, session['id'])
                        elif action.get('type') == 'commit_native_skill_builder':
                            if not self.native_builder:
                                raise Failure('native_skill_builder_missing')
                            try:
                                self.native_builder.commit(task)
                            finally:
                                if self.native_builder.builder.created:
                                    session_report['created_agent'] = self.native_builder.builder.created
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
                                    changes = resource_changes(action, variables, knowledge)
                                    prior_version = builder.created['version'] if builder.created else None
                                    builder.revise_published(task, name, changes)
                                    added = [builder.require_resource('knowledge_bases', resource_id)
                                             for resource_id in changes.get('knowledge_bases', {}).get('add', [])]
                                    revised = builder.commit(added, changes=changes)
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
                                    elif action['type'] in {'create_builder_agent', 'create_builder_agent_and_check_lifecycle'}:
                                        builder.commit()
                                        if action['type'] == 'create_builder_agent_and_check_lifecycle':
                                            builder.check_lifecycle()
                            finally:
                                if builder.created:
                                    session_report['created_agent'] = builder.created
                        elif action.get('type') in {'deliver_xlsx_with_skill', 'deliver_ooxml_with_skill', 'deliver_pdf_with_skill'}:
                            self.check_skill(action['skill_id'], final_entry['task_id'], session['id'], turn['phase'], checks)
                            filename, content = action['filename'].format_map(variables), action['content'].format_map(variables)
                            if action['type'] == 'deliver_xlsx_with_skill':
                                artifact_delivered = check_xlsx_artifact(self, cid, final_entry['task_id'],
                                    filename, content, checks, session['id'])
                            elif action['type'] == 'deliver_pdf_with_skill':
                                artifact_delivered = check_pdf_artifact(self, cid, final_entry['task_id'],
                                    filename, content, checks, session['id'])
                            else:
                                artifact_delivered = check_ooxml_artifact(self, cid, final_entry['task_id'],
                                    filename, content, action['kind'], checks, session['id'])
                        else:
                            raise Failure('invalid_case_action')
                    if turn.get('lifecycle_action') is not None:
                        if not resource_lifecycle or turn.get('lifecycle_check') is None:
                            raise Failure('invalid_lifecycle_action')
                        resource_lifecycle.apply(turn['lifecycle_action'])
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
        except NativeSkillUnavailable as error:
            self.report['status'] = 'failed'
            self.report['blocked'] = True
            self.report['failures'].append({'stage': 'native_skill_source', 'reason': str(error)})
        except ChannelUnavailable as error:
            self.report['status'] = 'failed'
            self.report['blocked'] = True
            self.report['failures'].append({'stage': 'channel_preflight', 'reason': str(error)})
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
            if self.native_skill:
                self.native_skill.progress.finish()
            cleanup_started = time.monotonic()
            tasks_confirmed = (self.pending_conversation is None and
                               all(task.get('state') in TERMINAL for task in self.tasks))
            if tasks_confirmed:
                for builder in builders.values():
                    builder.cleanup()
            elif builders:
                self.report['harness_errors'].append(
                    {'stage': 'cleanup_agent', 'reason': 'dependent_task_unconfirmed'})
            if knowledge:
                dependencies_confirmed = (tasks_confirmed and not any(
                    item.get('stage') == 'cleanup_agent' for item in self.report['harness_errors']))
                for fixture in knowledge.values():
                    fixture.cleanup(dependencies_confirmed=dependencies_confirmed)
            if database:
                database.cleanup(dependencies_confirmed=tasks_confirmed)
            if skill_journey:
                skill_journey.cleanup(dependencies_confirmed=tasks_confirmed)
            for channel in reversed(list(channel_journeys.values())):
                channel.cleanup(dependencies_confirmed=tasks_confirmed)
            if tool_journey:
                tool_journey.cleanup(dependencies_confirmed=tasks_confirmed)
            if resource_lifecycle:
                resource_lifecycle.cleanup(dependencies_confirmed=tasks_confirmed)
            if builder_input:
                builder_input.cleanup(dependencies_confirmed=tasks_confirmed)
            if package_journey:
                package_journey.cleanup(dependencies_confirmed=tasks_confirmed)
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
