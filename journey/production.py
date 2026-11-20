"""Service-account authentication and resource bindings for production Journey-3."""

import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .client import Client, Failure, NoRedirect

BASE_URL = 'https://api.moi.matrixorigin.cn/newmoi'
TOKEN_URL = 'https://uc.moi.matrixorigin.cn/api/v1/uc/realms/moi/protocol/openid-connect/token'


def is_production():
    return os.environ.get('MOI_ENVIRONMENT', 'qa') == 'production'


class ServiceAccount:
    def __init__(self, client_id, secret):
        if not re.fullmatch(r'uc-sa-[a-z0-9]+', client_id or '') or not secret or any(c.isspace() for c in secret):
            raise Failure('production_service_account_credentials_missing_or_invalid')
        self.form = urlencode({'grant_type': 'client_credentials',
                               'client_id': client_id, 'client_secret': secret}).encode()
        self.opener = build_opener(NoRedirect())
        self.token = None
        self.expires = 0

    def bearer(self, remaining):
        now = time.monotonic()
        if self.expires > now + 30:
            return self.token
        request = Request(TOKEN_URL, data=self.form, headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
        })
        try:
            with self.opener.open(request, timeout=min(30, remaining)) as response:
                grant = json.loads(response.read(65536))
        except HTTPError as error:
            error.close()
            raise Failure('production_service_account_token_rejected', error.code) from None
        except (URLError, OSError, ValueError):
            raise Failure('production_service_account_token_unavailable') from None
        token = grant.get('access_token') if isinstance(grant, dict) else None
        expires = grant.get('expires_in') if isinstance(grant, dict) else None
        token_type = grant.get('token_type') if isinstance(grant, dict) else None
        if (not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token)
                or type(expires) is not int or expires <= 0
                or not isinstance(token_type, str) or token_type.lower() != 'bearer'):
            raise Failure('production_service_account_grant_invalid')
        self.token, self.expires = token, now + expires
        return token


def knowledge_client(timeout, observer=None):
    base_url = os.environ['MOI_BASE_URL']
    if not is_production():
        return Client(base_url, os.environ['MOI_PAT'], timeout=timeout, observer=observer)
    if base_url.rstrip('/') != BASE_URL:
        raise Failure('production_base_url_mismatch')
    workspace = os.environ.get('MOI_J3_WORKSPACE_ID', '')
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', workspace):
        raise Failure('production_journey3_workspace_id_invalid')
    method = os.environ.get('MOI_J3_AUTH_MODE') or 'service-account'
    if method == 'pat':
        pat = os.environ.get('MOI_PAT')
        if not pat:
            raise Failure('production_pat_missing')
        return Client(BASE_URL, pat, timeout=timeout, observer=observer, locked_workspace=workspace)
    if method != 'service-account':
        raise Failure('production_auth_mode_invalid')
    account = ServiceAccount(os.environ.get('MOI_J3_CLIENT_ID'), os.environ.get('MOI_J3_CLIENT_SECRET'))
    return Client(BASE_URL, None, timeout=timeout, observer=observer,
                  bearer=account.bearer, locked_workspace=workspace)


def bindings():
    try:
        value = json.loads(os.environ.get('MOI_J3_BINDINGS', ''))
    except ValueError:
        raise Failure('production_journey3_resource_bindings_missing_or_invalid') from None
    if not isinstance(value, dict) or not all(isinstance(value.get(key), dict) and value[key]
            for key in ('corpora', 'document_agents', 'operations_agents')):
        raise Failure('production_journey3_resource_bindings_missing_or_invalid')
    return value


def binding(mapping, key):
    if key not in mapping:
        raise Failure('production_journey3_resource_binding_missing')
    return mapping[key]


def validate_selection(workspaces):
    target = os.environ.get('MOI_ENVIRONMENT', 'qa')
    if target == 'qa':
        return
    if (target != 'production' or workspaces != ['journey-3']
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'):
        raise SystemExit('Production runs require manual dispatch on main with journey-3 selected.')


if __name__ == '__main__':
    # Validate the configuration before the runners start any production cases.
    try:
        bindings()
        from .hello import discover_workspace
        discover_workspace(knowledge_client(60), 'journey-3')
    except Failure as error:
        print(f'::error::{error.code}')
        raise SystemExit(1)
    print('Production journey-3 workspace authenticated and bindings configured')
