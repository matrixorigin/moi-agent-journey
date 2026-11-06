"""Feishu member traffic; local CLI credentials and CI bearer credentials stay private."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .client import Failure, NoRedirect

ORIGIN = 'https://open.feishu.cn'


def member_config():
    try:
        value = json.loads(os.environ['MOI_J5_FEISHU_TARGET'])
        fields = ('workspace_id', 'agent_id', 'ingress_connection_id', 'app_id', 'chat_id', 'user_open_id', 'bot_open_id')
        if any(not isinstance(value.get(k), str) or not value[k] for k in fields):
            raise ValueError('missing_target_field')
        return {k: value[k] for k in fields}
    except (KeyError, ValueError, TypeError):
        raise Failure('invalid_feishu_test_target') from None


class Feishu:
    def __init__(self, evidence, *, deadline, profile=None, binary='lark-cli',
                 access_token=None, app_id=None, app_secret=None):
        self.evidence, self.deadline = evidence, deadline
        self.profile, self.binary = profile, binary
        self.access_token, self.app_id, self.app_secret = access_token, app_id, app_secret
        self.bot_token = None
        self.sequence = 0
        if not profile and not all((access_token, app_id, app_secret)):
            raise Failure('feishu_member_configuration_missing')

    def remaining(self):
        seconds = min(30, self.deadline - time.monotonic())
        if seconds <= 0:
            raise Failure('deadline_exceeded')
        return seconds

    def _cli(self, args, body=None):
        env = {**os.environ, 'LARKSUITE_CLI_NO_UPDATE_NOTIFIER': '1',
               'LARKSUITE_CLI_NO_SKILLS_NOTIFIER': '1'}
        try:
            process = subprocess.run([self.binary, '--profile', self.profile] + args,
                input=json.dumps(body, ensure_ascii=False) if body is not None else None,
                capture_output=True, text=True, timeout=self.remaining(), env=env)
        except subprocess.TimeoutExpired:
            # A lost mutation receipt is never permission to resend.
            raise Failure('feishu_request_outcome_unknown') from None
        try:
            result = json.loads(process.stdout if process.returncode == 0 else process.stderr)
        except ValueError:
            raise Failure('feishu_cli_invalid_response') from None
        if process.returncode or result.get('ok') is not True:
            error = result.get('error', {})
            raise Failure('feishu_api_error', details={key: error.get(key) for key in ('type', 'subtype', 'code')})
        return result['data']

    def _http(self, method, path, *, token=None, params=None, body=None, raw=None, content_type=None):
        url = ORIGIN + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if body is not None:
            raw, content_type = json.dumps(body, ensure_ascii=False).encode(), 'application/json'
        if content_type:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(url, method=method, headers=headers, data=raw)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.remaining()) as response:
                payload = response.read(8_000_001)
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise Failure('feishu_http_error', status) from None
        except (urllib.error.URLError, OSError):
            raise Failure('feishu_request_outcome_unknown') from None
        if len(payload) > 8_000_000:
            raise Failure('feishu_response_too_large')
        try:
            value = json.loads(payload)
        except ValueError:
            raise Failure('feishu_invalid_response') from None
        if value.get('code') != 0:
            raise Failure('feishu_api_error', details={'code': value.get('code')})
        return value

    def _bot(self):
        if self.bot_token is None:
            auth = self._http('POST', '/open-apis/auth/v3/tenant_access_token/internal',
                              body={'app_id': self.app_id, 'app_secret': self.app_secret})
            self.bot_token = auth.get('tenant_access_token')
            if not isinstance(self.bot_token, str) or not self.bot_token:
                raise Failure('feishu_bot_token_missing')
            self.evidence.secrets = tuple(sorted(set(self.evidence.secrets + (self.bot_token,)), key=len, reverse=True))
        return self.bot_token

    def request(self, method, path, *, params=None, body=None, actor='user'):
        if actor not in {'user', 'bot'} or not (path.startswith(('/open-apis/im/v1/', '/open-apis/bot/v3/')) or
                path == '/open-apis/application/v6/scopes'):
            raise Failure('invalid_feishu_request')
        started = time.monotonic()
        if self.profile:
            args = ['api', method, path, '--as', actor]
            if params:
                args += ['--params', json.dumps(params)]
            if body is not None:
                args += ['--data', '-']
            data = self._cli(args, body)
        else:
            data = self._http(method, path, params=params, body=body,
                              token=self.access_token if actor == 'user' else self._bot()).get('data', {})
        self.sequence += 1
        self.evidence.write(f'feishu/{self.sequence:04d}.json', {'method': method, 'path': path,
            'actor': actor, 'params': params, 'request': body, 'response': data,
            'seconds': round(time.monotonic() - started, 3)})
        return data

    def send(self, chat_id, content, *, kind='text', key, reply_to=None):
        body = {'msg_type': kind, 'content': json.dumps(content, ensure_ascii=False), 'uuid': key}
        if reply_to:
            body['reply_in_thread'] = True
            result = self.request('POST', '/open-apis/im/v1/messages/' +
                                  urllib.parse.quote(reply_to, safe='') + '/reply', body=body)
        else:
            body['receive_id'] = chat_id
            result = self.request('POST', '/open-apis/im/v1/messages',
                                  params={'receive_id_type': 'chat_id'}, body=body)
        if result.get('chat_id') != chat_id or not result.get('message_id'):
            raise Failure('feishu_send_receipt_identity_mismatch')
        return result

    def messages(self, container_id, *, thread=False, start_time=None):
        params = {'container_id_type': 'thread' if thread else 'chat', 'container_id': container_id,
                  'page_size': 50, 'sort_type': 'ByCreateTimeDesc', 'card_msg_content_type': 'user_card_content'}
        if start_time is not None:
            params['start_time'] = str(start_time)
        items = []
        for _ in range(20):
            page = self.request('GET', '/open-apis/im/v1/messages', params=params)
            items.extend(page.get('items', []))
            if not page.get('has_more'):
                return items
            cursor = page.get('page_token')
            if not cursor or cursor == params.get('page_token'):
                raise Failure('feishu_invalid_pagination')
            params['page_token'] = cursor
        raise Failure('feishu_message_history_limit')

    def upload(self, path, *, image=False):
        path = Path(path)
        fields = {'image_type': 'message'} if image else {'file_type': 'stream', 'file_name': path.name}
        resource = 'images' if image else 'files'
        if self.profile:
            data = self._cli(['im', resource, 'create', '--as', 'bot', '--data', json.dumps(fields),
                              '--file', str(path.resolve()), '--json'])
        else:
            boundary = 'journey-' + uuid.uuid4().hex
            parts = []
            for key, value in fields.items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
            filename = path.name
            if any(c in filename for c in '\r\n"'):
                raise Failure('invalid_feishu_upload_filename')
            field = 'image' if image else 'file'
            parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
                      path.read_bytes(), f'\r\n--{boundary}--\r\n'.encode()]
            data = self._http('POST', '/open-apis/im/v1/' + resource, token=self._bot(),
                raw=b''.join(parts), content_type='multipart/form-data; boundary=' + boundary)['data']
        self.evidence.write('feishu/upload-' + path.name + '.json', data)
        key = data.get('image_key' if image else 'file_key')
        if not isinstance(key, str) or not key:
            raise Failure('feishu_upload_key_missing')
        return key


def reply_content(message):
    """Only visible reply nodes count; config and hidden metadata never prove an answer."""
    try:
        body = json.loads(message['body']['content'])
    except (KeyError, TypeError, ValueError):
        raise Failure('invalid_feishu_message_body') from None
    if message.get('msg_type') == 'text':
        return body.get('text', ''), True
    if message.get('msg_type') != 'interactive':
        return '', False
    values = []
    def visit(node):
        if isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, dict):
            if node.get('tag') in {'markdown', 'plain_text', 'lark_md'} and isinstance(node.get('content'), str):
                values.append(node['content'])
            for key, value in node.items():
                if key not in {'content', 'config', 'value', 'behaviors'}:
                    visit(value)
    visit(body.get('body', {}).get('elements', body.get('elements', [])))
    return '\n'.join(values).replace('\u200b', '').strip(), body.get('config', {}).get('streaming_mode') is False
