"""Journey-3 MCP adapter for five scoped semantic-model operations."""

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PRODUCTION_BASE_URL = 'https://api.moi.matrixorigin.cn/newmoi'
OPERATIONS = {
    'newmoi_get_semantic_models_model_id_export': ('GET', 'export', set()),
    'newmoi_get_semantic_models_model_id_entries': ('GET', 'entries', {'page_size', 'page_token', 'domain', 'kind'}),
    'newmoi_post_semantic_models_model_id_validate': ('POST', 'validate', set()),
    'newmoi_post_semantic_models_model_id_entries': ('POST', 'entries', {'domain', 'kind', 'key', 'tables', 'spec'}),
    'newmoi_put_semantic_models_model_id_entries_entry_id': ('PUT', 'entries', {'entry_id', 'domain', 'kind', 'key', 'tables', 'spec'}),
}


class ToolError(Exception):
    """A bounded, safe-to-report tool error."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def resolve_call(name, arguments, workspace, read_models, scratch_model):
    operation = OPERATIONS.get(name) if isinstance(name, str) else None
    if operation is None or not isinstance(arguments, dict):
        raise ToolError('unknown_tool_or_invalid_arguments')
    method, endpoint, fields = operation
    if set(arguments) - fields - {'workspace_id', 'model_id'}:
        raise ToolError('unexpected_tool_arguments')
    if arguments.get('workspace_id') != workspace:
        raise ToolError('workspace_outside_journey3')
    model = arguments.get('model_id')
    writable = method == 'PUT' or (method == 'POST' and endpoint == 'entries')
    allowed = {scratch_model} if writable else read_models | {scratch_model}
    if type(model) is not int or model not in allowed:
        raise ToolError('write_requires_scratch_model' if writable else 'model_outside_journey3')
    payload = {key: arguments[key] for key in sorted(fields) if key in arguments}
    path = f'/semantic-models/{model}/{endpoint}'
    if writable and not {'kind', 'key', 'spec'} <= payload.keys():
        raise ToolError('required_entry_fields_missing')
    if method == 'PUT':
        entry = payload.pop('entry_id', None)
        if type(entry) is not int or entry <= 0:
            raise ToolError('invalid_entry_id')
        path += f'/{entry}'
    if method == 'GET':
        if any(type(value) not in (int, str) for value in payload.values()):
            raise ToolError('invalid_list_filter')
        return method, path + ('?' + urlencode(payload) if payload else ''), None
    return method, path, payload


class Bridge:
    def __init__(self, workspace, read_models, scratch_model, tools, upstream):
        self.workspace = workspace
        self.read_models = read_models
        self.scratch_model = scratch_model
        self.tools = tools
        self.upstream = upstream

    def call(self, params):
        try:
            method, path, body = resolve_call(params.get('name'), params.get('arguments'),
                                              self.workspace, self.read_models, self.scratch_model)
            data = self.upstream(method, path, body)
            return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}],
                    'structuredContent': data}
        except ToolError as error:
            return {'content': [{'type': 'text', 'text': str(error)}], 'isError': True}


def product_upstream(base_url, api_key, workspace, environment):
    if environment == 'production' and base_url.rstrip('/') != PRODUCTION_BASE_URL:
        raise ValueError('production upstream must match the production product endpoint')
    parsed = urlsplit(base_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('upstream must be a fixed HTTPS product endpoint')
    opener = build_opener(NoRedirect())

    def call(method, path, body):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(base_url.rstrip('/') + path, method=method, data=data, headers={
            'X-API-Key': api_key, 'X-Workspace-ID': workspace,
            'Accept': 'application/json', 'Content-Type': 'application/json',
        })
        try:
            with opener.open(request, timeout=25) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            error.close()
            raise ToolError(f'{environment}_semantic_http_{error.code}') from None
        except (URLError, OSError):
            raise ToolError(f'{environment}_semantic_unavailable') from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ToolError(f'{environment}_semantic_response_too_large')
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ToolError(f'{environment}_semantic_invalid_response') from None
        if not isinstance(envelope, dict) or envelope.get('code') != 'OK' or not isinstance(envelope.get('data'), dict):
            raise ToolError(f'{environment}_semantic_rejected_request')
        return envelope['data']

    return call


def handler_for(bridge, key, *, path='/mcp', environment='qa'):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(30)
            super().setup()

        def log_message(self, _format, *_args):
            pass

        def reply(self, status, payload=None):
            data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b''
            self.send_response(status)
            if payload is not None:
                self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            self.reply(204 if self.path == '/healthz' else 405)

        def do_POST(self):
            if self.path != path:
                self.reply(404)
                return
            supplied = self.headers.get('X-API-Key', '').encode()
            if not hmac.compare_digest(supplied, key):
                self.reply(401)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= MAX_REQUEST_BYTES:
                    self.reply(413)
                    return
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
                    raise ValueError('invalid MCP request')
            except (ValueError, UnicodeError):
                self.reply(400)
                return
            method = request.get('method')
            params = request.get('params', {})
            if not isinstance(params, dict):
                self.reply(400)
                return
            if 'id' not in request:
                self.reply(202)
                return
            reply = {'jsonrpc': '2.0', 'id': request['id']}
            if method == 'initialize':
                result = {'protocolVersion': params.get('protocolVersion', '2025-03-26'),
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': f'journey3-{environment}-semantics', 'version': '1.0.0'}}
            elif method == 'tools/list':
                result = {'tools': bridge.tools}
            elif method == 'tools/call':
                result = bridge.call(params)
            elif method == 'ping':
                result = {}
            else:
                self.reply(200, {**reply, 'error': {'code': -32601, 'message': 'Method not found'}})
                return
            self.reply(200, {**reply, 'result': result})

    return Handler


def main():
    environment = os.environ.get('J3_ENVIRONMENT', 'qa')
    if environment not in {'qa', 'production'}:
        raise ValueError('invalid Journey-3 environment')
    workspace = os.environ['J3_WORKSPACE_ID']
    read_models = {int(value) for value in os.environ['J3_READ_MODEL_IDS'].split(',')}
    scratch_model = int(os.environ['J3_SCRATCH_MODEL_ID'])
    if scratch_model in read_models:
        raise ValueError('scratch model cannot be an evaluation snapshot')
    tools = json.loads(Path(__file__).with_name('tools.json').read_text())
    if {tool['name'] for tool in tools} != set(OPERATIONS):
        raise ValueError('unexpected MCP tool catalog')
    variable_prefix = 'J3_PROD' if environment == 'production' else 'J3_QA'
    upstream = product_upstream(os.environ[variable_prefix + '_BASE_URL'], os.environ[variable_prefix + '_API_KEY'],
                                workspace, environment)
    bridge = Bridge(workspace, read_models, scratch_model, tools, upstream)
    key = os.environ['J3_BRIDGE_KEY'].encode()
    if len(key) < 32:
        raise ValueError('MCP authentication key is too short')
    path = '/journey-3-semantic-mcp' if environment == 'production' else '/mcp'
    ThreadingHTTPServer(('0.0.0.0', 8080), handler_for(bridge, key, path=path, environment=environment)).serve_forever()


if __name__ == '__main__':
    main()
