import contextlib
import io
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from journey.catalog import load_case, load_suite, plan_suite
from journey.client import Failure
from journey.evidence import Evidence, configured_secrets
from journey.feishu import Feishu, reply_content
from journey.feishu_case import FeishuExecution, number_image, group_attachment_permission, tool_checks
from journey.feishu_auth import main as auth_main, save_refresh_token
from journey.report import finish_case
from journey.publish import prepare_publication

TARGET = {'workspace_id': 'ws', 'agent_id': 'agent', 'ingress_connection_id': 'ingress',
          'app_id': 'app-1', 'chat_id': 'chat-1', 'user_open_id': 'member-1', 'bot_open_id': 'bot-1'}


def card(text, **overrides):
    value = {'message_id': 'bot-message', 'chat_id': 'chat-1', 'msg_type': 'interactive',
             'sender': {'sender_type': 'app', 'id': 'app-1'},
             'body': {'content': json.dumps({'schema': '2.0', 'body': {'elements': [
                 {'tag': 'markdown', 'content': '\u200b' + text}]}, 'config': {'streaming_mode': False}})}}
    value.update(overrides)
    return value


class ProtocolTests(unittest.TestCase):
    def test_only_visible_finished_card_content_proves_delivery(self):
        value = card('REPLY')
        self.assertEqual(reply_content(value), ('REPLY', True))
        body = json.loads(value['body']['content'])
        body['config']['streaming_mode'] = True
        body['body']['elements'].append({'tag': 'button', 'value': {'text': 'HIDDEN_MARKER'}})
        value['body']['content'] = json.dumps(body)
        text, final = reply_content(value)
        self.assertFalse(final)
        self.assertNotIn('HIDDEN_MARKER', text)

    def test_member_send_is_idempotent_and_uses_exact_recipient(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Feishu(Evidence(tmp), deadline=time.monotonic() + 30, profile='test-profile')
            response = {'ok': True, 'identity': 'user', 'data': {'chat_id': 'chat-1', 'message_id': 'sent'}}
            with patch('journey.feishu.subprocess.run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(response), stderr='')) as run:
                client.send('chat-1', {'text': 'hello'}, key='same-key')
            args = run.call_args.args[0]
            body = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(args[-2:], ['--data', '-'])
            self.assertEqual(args[args.index('--as') + 1], 'user')
            self.assertEqual(body['uuid'], 'same-key')
            self.assertEqual(body['receive_id'], 'chat-1')

    def test_uncertain_send_is_not_retried(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            client = Feishu(Evidence(tmp), deadline=time.monotonic() + 30, profile='test-profile')
            with patch('journey.feishu.subprocess.run', side_effect=subprocess.TimeoutExpired('lark-cli', 1)) as run:
                with self.assertRaisesRegex(Failure, 'outcome_unknown'):
                    client.send('chat-1', {'text': 'hello'}, key='same-key')
            self.assertEqual(run.call_count, 1)

    def test_cli_error_does_not_copy_credentials_from_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Feishu(Evidence(tmp), deadline=time.monotonic() + 30, profile='test-profile')
            error = {'ok': False, 'error': {'code': 123, 'type': 'api', 'message': 'private-token'}}
            with patch('journey.feishu.subprocess.run', return_value=SimpleNamespace(
                    returncode=1, stdout='', stderr=json.dumps(error))):
                with self.assertRaises(Failure) as caught:
                    client.request('GET', '/open-apis/im/v1/messages')
            self.assertEqual(caught.exception.details['code'], 123)
            self.assertNotIn('private-token', repr(caught.exception.details))

    def test_fresh_images_have_different_content_and_valid_png(self):
        first, second = number_image('123456'), number_image('654321')
        self.assertTrue(first.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertNotEqual(first, second)
        import struct, zlib
        pos = 8
        while pos < len(first):
            size = struct.unpack('!I', first[pos:pos + 4])[0]
            block = first[pos + 4:pos + 8 + size]
            self.assertEqual(zlib.crc32(block) & 0xffffffff, struct.unpack('!I', first[pos + 8 + size:pos + 12 + size])[0])
            pos += 12 + size

    def test_generated_png_uses_the_existing_binary_publication_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'results/J5-07/artifacts/inputs/test.png'
            path.parent.mkdir(parents=True)
            path.write_bytes(number_image('123456'))
            self.assertEqual(prepare_publication(root / 'results', root / 'public', []), [])
            self.assertEqual((root / 'public/J5-07/artifacts/inputs/test.png').read_bytes(), path.read_bytes())

    def test_ci_http_mode_normalizes_the_real_api_envelope_without_recording_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Feishu(Evidence(tmp), deadline=time.monotonic() + 30,
                            access_token='private-access', app_id='app', app_secret='private-app')
            data = io.BytesIO(json.dumps({'code': 0, 'data': {'message_id': 'sent', 'chat_id': 'chat-1'}}).encode())
            opener = SimpleNamespace(open=lambda request, **kwargs: data)
            with patch('journey.feishu.urllib.request.build_opener', return_value=opener):
                self.assertEqual(client.send('chat-1', {'text': 'hello'}, key='unique')['message_id'], 'sent')
            records = '\n'.join(p.read_text() for p in Path(tmp).rglob('*.json'))
            self.assertNotIn('private-access', records)
            self.assertNotIn('private-app', records)


class FixtureQA:
    """Product DTOs based on the observed member-message, runtime and event APIs."""
    def __init__(self, state='completed', duplicate=False):
        self.deadline, self.requests = time.monotonic() + 30, 0
        self.state, self.duplicate, self.tasks, self.headers = state, duplicate, {}, {}

    def product(self, method, path, body=None, **kwargs):
        self.requests += 1
        if path.startswith('/workspaces?'):
            return {'workspaces': [{'id': 'ws', 'name': 'journey-5', 'status': 'ACTIVE'}]}
        if path == '/system/versions':
            return {'backend_components': [{'component': 'moi-backend', 'version': 'v1', 'git_commit': 'build-1'}]}
        if '/ingress-connections/' in path:
            return {'enabled': True, 'provider': 'feishu', 'observed_state': 'connected',
                    'target': {'kind': 'agent', 'workspace_id': 'ws', 'id': 'agent'},
                    'provider_config': {'app_id': 'app-1'}}
        if '/conversations/' in path:
            items = [{'task_id': tid, 'role': 'user', 'parts': [{'kind': 'text', 'text': task['input']}]} for tid, task in self.tasks.items()]
            return {'items': items, 'total': len(items)}
        if '/events?' in path:
            tid = path.split('/agent-runtime-tasks/')[1].split('/')[0]
            after = int(parse_qs(urlsplit(path).query)['after_seq'][0])
            items = [{'task_id': tid, 'seq': 1, 'kind': 'lifecycle', 'a2a_state': self.state,
                      'payload': {'state': self.state}}]
            return {'items': [item for item in items if item['seq'] > after]}
        if '/agent-runtime-tasks?' in path:
            return {'items': [self.runtime(tid) for tid in self.tasks], 'total': len(self.tasks)}
        if '/agent-runtime-tasks/' in path:
            tid = path.split('/agent-runtime-tasks/')[1].split('?')[0]
            return self.runtime(tid)
        raise AssertionError(path)

    def runtime(self, tid):
        return {'id': tid, 'context_id': 'ctx', 'state': self.state, 'event_cursor': 1,
                'trace_id': 'trace-' + tid, **({'error': {'error': 'runtime usage rejected'}} if self.state == 'failed' else {})}

    def rpc(self, selector, method, params):
        return {'kind': 'task', 'id': params['id'], 'contextId': 'ctx', 'status': {'state': self.state}}


class FixtureMember:
    def __init__(self, qa, mode='normal'):
        self.qa, self.mode, self.items = qa, mode, []

    def send(self, chat_id, content, **kwargs):
        prompt = content['text']
        request_id = re.search(r'请求编号 (J5_\w+)。', prompt)[1]
        for n in range(2 if self.qa.duplicate else 1):
            self.qa.tasks['task-' + str(n)] = {'input': prompt}
        reply = card('The Agent Team could not complete this request (runtime_error).' if self.qa.state == 'failed'
                     else request_id + ' HELLO_OK')
        if self.mode == 'wrong_bot':
            reply['sender']['id'] = 'another-app'
        self.items = [{'message_id': 'user-message', 'sender': {'sender_type': 'user', 'id': 'member-1'}}, reply]
        return {'chat_id': chat_id, 'message_id': 'user-message'}

    def messages(self, *args, **kwargs):
        return self.items


class RecorderTests(unittest.TestCase):
    def test_missing_bot_group_permission_is_blocked_before_uploading_or_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa, member = FixtureQA(), SimpleNamespace()
            member.request = lambda *args, **kwargs: {'scopes': [
                {'scope_name': 'im:message.group_at_msg:readonly', 'scope_type': 'tenant', 'grant_status': 1}]}
            case = load_case('J5-07')
            execution = FeishuExecution(qa, case, Evidence(Path(tmp) / 'J5-07'), tmp, case['model'], member, TARGET)
            execution.run()
            final = finish_case('J5-07', Path(tmp))
            self.assertEqual(final['test_status'], 'completed')
            self.assertEqual(final['functional']['state'], 'blocked')
            self.assertEqual(final['tasks'], [])
            self.assertEqual(final['channel']['turns'], [])

    def run_fixture(self, directory, *, state='completed', mode='normal', duplicate=False):
        qa = FixtureQA(state, duplicate)
        case = load_case('J5-01')
        execution = FeishuExecution(qa, case, Evidence(directory / 'J5-01'), directory, case['model'], FixtureMember(qa, mode), TARGET)
        finish = execution.finish_turn
        with patch.object(execution, 'finish_turn', side_effect=lambda turn: finish(turn, wait_seconds=0.02)):
            execution.run()
        return execution.report, finish_case('J5-01', directory)

    def test_real_reply_and_matching_persisted_input_produce_complete_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, final = self.run_fixture(Path(tmp))
            self.assertEqual(raw['status'], 'passed')
            self.assertEqual(final['functional']['state'], 'pass')
            self.assertEqual(final['test_status'], 'completed')
            self.assertEqual(final['delivery']['state'], 'delivered')
            self.assertTrue(final['capture_complete'])
            self.assertIn('trace-task-0', (Path(tmp) / 'J5-01/case-summary.md').read_text())
            self.assertTrue((Path(tmp) / 'J5-01/tasks/task-0/messages.json').exists())

    def test_qa_runtime_failure_is_a_product_failure_with_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, final = self.run_fixture(Path(tmp), state='failed')
            self.assertEqual(raw['status'], 'failed')
            self.assertEqual(final['functional']['state'], 'fail')
            self.assertEqual(final['test_status'], 'completed')
            self.assertEqual(final['agent']['state'], 'abnormal')
            self.assertEqual(final['tasks'][0]['runtime_error']['error'], 'runtime usage rejected')

    def test_another_bot_cannot_satisfy_a_case_even_with_the_correct_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, final = self.run_fixture(Path(tmp), mode='wrong_bot')
            self.assertEqual(raw['status'], 'failed')
            self.assertEqual(final['functional']['state'], 'fail')
            self.assertFalse(raw['sessions'][0]['answer_delivered'])

    def test_duplicate_qa_tasks_are_captured_and_reported_as_product_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw, final = self.run_fixture(Path(tmp), duplicate=True)
            self.assertEqual(raw['status'], 'failed')
            self.assertEqual(len(final['tasks']), 2)
            self.assertEqual(final['test_status'], 'completed')
            self.assertTrue(final['capture_complete'])

    def test_history_does_not_cache_a_not_yet_persisted_user_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa = FixtureQA()
            case = load_case('J5-01')
            execution = FeishuExecution(qa, case, Evidence(tmp), tmp, case['model'], FixtureMember(qa), TARGET)
            execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'agent', 'agent_workspace_id': 'ws'}
            task = {'id': 'task-0', 'context_id': 'ctx'}
            self.assertEqual(execution.history(task), [])
            qa.tasks['task-0'] = {'input': 'now persisted'}
            self.assertEqual(execution.history(task)[0]['parts'][0]['text'], 'now persisted')

    def test_inventory_stops_at_old_updates_without_scanning_the_whole_agent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            qa = FixtureQA()
            case = load_case('J5-01')
            execution = FeishuExecution(qa, case, Evidence(tmp), tmp, case['model'], FixtureMember(qa), TARGET)
            execution.root, execution.selector = '/workspaces/ws', {'agent_id': 'agent', 'agent_workspace_id': 'ws'}
            page = {'items': [{'id': 'older-created-but-recent-update', 'created_at': '2020-01-01T00:00:00Z',
                               'updated_at': '2026-01-02T00:00:00Z'},
                              {'id': 'later-created', 'created_at': '2025-01-01T00:00:00Z',
                               'updated_at': '2026-01-01T00:00:00Z'}], 'total': 100000}
            with patch.object(qa, 'product', return_value=page) as read:
                self.assertEqual(len(execution.inventory()), 2)
            self.assertEqual(read.call_count, 1)


class CredentialTests(unittest.TestCase):
    def test_rotation_is_saved_to_environment_secret_before_access_token_is_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / 'github-env'
            env = {'MOI_J5_FEISHU_TARGET': json.dumps(TARGET), 'MOI_J5_FEISHU_REFRESH_TOKEN': 'old-refresh',
                   'MOI_J5_CREDENTIAL_WRITER_TOKEN': 'limited-writer', 'MOI_J5_FEISHU_APP_SECRET': 'app-secret',
                   'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_ENV': str(env_file)}
            value = {'refresh_token': 'next-refresh', 'access_token': 'fresh-access', 'expires_in': 7200, 'token_type': 'Bearer'}
            seen = []
            def save(token, *args):
                self.assertFalse(env_file.exists())
                seen.append(token)
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                    patch('journey.feishu_auth.save_refresh_token', side_effect=save), \
                    patch('journey.feishu_auth.refresh_member', return_value=value):
                self.assertEqual(auth_main(), 0)
            self.assertEqual(seen, ['old-refresh', 'next-refresh'])
            self.assertIn('MOI_J5_FEISHU_ACCESS_TOKEN=fresh-access', env_file.read_text())
            self.assertNotIn('next-refresh', env_file.read_text())

    def test_missing_write_permission_does_not_consume_the_refresh_token(self):
        env = {'MOI_J5_FEISHU_TARGET': json.dumps(TARGET), 'MOI_J5_FEISHU_REFRESH_TOKEN': 'old',
               'MOI_J5_CREDENTIAL_WRITER_TOKEN': 'writer', 'GITHUB_REPOSITORY': 'owner/repo'}
        with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                patch('journey.feishu_auth.save_refresh_token', side_effect=Failure('write_denied')), \
                patch('journey.feishu_auth.refresh_member') as refresh:
            self.assertEqual(auth_main(), 1)
        refresh.assert_not_called()

    def test_failed_successor_save_does_not_expose_access_token_or_retry_exchange(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / 'github-env'
            env = {'MOI_J5_FEISHU_TARGET': json.dumps(TARGET), 'MOI_J5_FEISHU_REFRESH_TOKEN': 'old',
                   'MOI_J5_CREDENTIAL_WRITER_TOKEN': 'writer', 'MOI_J5_FEISHU_APP_SECRET': 'app',
                   'GITHUB_REPOSITORY': 'owner/repo', 'GITHUB_ENV': str(env_file)}
            value = {'refresh_token': 'next', 'access_token': 'access', 'expires_in': 7200, 'token_type': 'Bearer'}
            with patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()), \
                    patch('journey.feishu_auth.save_refresh_token', side_effect=[None, Failure('write_failed')]), \
                    patch('journey.feishu_auth.refresh_member', return_value=value) as refresh:
                self.assertEqual(auth_main(), 1)
            self.assertFalse(env_file.exists())
            self.assertEqual(refresh.call_count, 1)

    def test_writer_uses_stdin_and_qa_environment(self):
        with patch('journey.feishu_auth.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            save_refresh_token('private-refresh', 'owner/repo', 'limited-token')
        args = run.call_args.args[0]
        self.assertNotIn('private-refresh', args)
        self.assertEqual(args[-2:], ['--env', 'qa'])
        self.assertEqual(run.call_args.kwargs['input'], 'private-refresh')

    def test_all_member_tokens_are_redacted_at_publication_boundaries(self):
        env = {'MOI_J5_FEISHU_ACCESS_TOKEN': 'private-access', 'MOI_J5_FEISHU_REFRESH_TOKEN': 'private-refresh',
               'MOI_J5_FEISHU_APP_SECRET': 'private-app', 'MOI_J5_CREDENTIAL_WRITER_TOKEN': 'private-writer'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env, clear=True):
            evidence = Evidence(tmp, configured_secrets())
            evidence.write('result.json', {'values': list(env.values())})
            text = (Path(tmp) / 'result.json').read_text()
            self.assertFalse(any(value in text for value in env.values()))


class SuiteTests(unittest.TestCase):
    def test_member_history_scope_does_not_grant_bot_attachment_events(self):
        self.assertFalse(group_attachment_permission([
            {'scope_type': 'user', 'scope_name': 'im:message.group_msg:get_as_user', 'grant_status': 1},
            {'scope_type': 'tenant', 'scope_name': 'im:message.group_at_msg:readonly', 'grant_status': 1}]))
        self.assertFalse(group_attachment_permission([
            {'scope_type': 'tenant', 'scope_name': 'im:message.group_msg', 'grant_status': 2}]))
        self.assertTrue(group_attachment_permission([
            {'scope_type': 'tenant', 'scope_name': 'im:message.group_msg', 'grant_status': 1}]))

    def test_observed_runtime_tool_id_and_only_the_exact_expected_error_are_accepted(self):
        analysis = {'metrics': {'by_tool': [{'tool': 'mcp__moi-tools__bash', 'succeeded': 1}]},
                    'errors': [{'tool': 'mcp__moi-tools__bash', 'task_id': 'task', 'call_id': 'call',
                                'errors': [{'kind': 'command_exit', 'code': '23', 'detail': 'J5_EXPECTED_FAILURE\n'}]}]}
        checks = tool_checks(analysis, require_error=True)
        self.assertTrue(all(check['passed'] for check in checks))
        self.assertEqual(checks[-1]['expected_tool_errors'][0]['code'], '23')
        analysis['errors'][0]['errors'][0]['detail'] = 'unrelated command error'
        self.assertFalse(tool_checks(analysis, require_error=True)[-1]['passed'])

    def test_ten_cases_share_one_serial_runner_and_keep_five_minute_task_budgets(self):
        suite = load_suite('journey-5')
        groups = plan_suite(suite)['include']
        self.assertEqual(suite['concurrency'], 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['case_ids'], [f'J5-{i:02d}' for i in range(1, 11)])
        self.assertTrue(groups[0]['needs_feishu'])
        self.assertTrue(all(load_case(cid)['timeout_seconds'] == 300 for cid in suite['cases']))
        for cid in suite['cases']:
            case = load_case(cid)
            phases = [s['id'] + ':' + t['phase'] for s in case['sessions'] for t in s['turns']]
            self.assertEqual(len(phases), len(set(phases)))
            if 'order' in case['feishu']:
                self.assertCountEqual(case['feishu']['order'], phases)


if __name__ == '__main__':
    unittest.main()
