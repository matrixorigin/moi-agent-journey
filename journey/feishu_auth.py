"""Rotate a CI-only Feishu member grant and commit its successor to Actions Secrets."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from .client import Failure, NoRedirect
from .feishu import member_config

REFRESH_URL = 'https://accounts.feishu.cn/oauth/v3/token'
SECRET_NAME = 'MOI_J5_FEISHU_REFRESH_TOKEN'


def save_refresh_token(token, repository, writer_token):
    """gh encrypts stdin using the qa environment key; no token enters argv or artifacts."""
    env = {**os.environ, 'GH_TOKEN': writer_token}
    result = subprocess.run(['gh', 'secret', 'set', SECRET_NAME, '--repo', repository, '--env', 'qa'],
        input=token, text=True, capture_output=True, timeout=30, env=env)
    if result.returncode:
        raise Failure('feishu_refresh_secret_write_failed')


def refresh_member(app_id, app_secret, refresh_token):
    request = urllib.request.Request(REFRESH_URL, method='POST',
        headers={'Content-Type': 'application/json'}, data=json.dumps({
            'grant_type': 'refresh_token', 'client_id': app_id,
            'client_secret': app_secret, 'refresh_token': refresh_token}).encode())
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            value = json.loads(response.read(100_001))
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise Failure('feishu_member_refresh_http_error', status) from None
    except (urllib.error.URLError, OSError, ValueError):
        # The one-use refresh token may have been consumed. Never retry the exchange.
        raise Failure('feishu_member_refresh_outcome_unknown') from None
    if value.get('code') != 0 or not isinstance(value.get('refresh_token'), str) or not value['refresh_token']:
        raise Failure('feishu_member_refresh_failed', details={'code': value.get('code')})
    return value


def main():
    try:
        target = member_config()
        old = os.environ['MOI_J5_FEISHU_REFRESH_TOKEN']
        writer = os.environ['MOI_J5_CREDENTIAL_WRITER_TOKEN']
        repository = os.environ['GITHUB_REPOSITORY']
        if not old or not writer:
            raise Failure('feishu_ci_credential_missing')
        # Verify encryption/write access before spending the single-use refresh token.
        save_refresh_token(old, repository, writer)
        value = refresh_member(target['app_id'], os.environ['MOI_J5_FEISHU_APP_SECRET'], old)
        next_refresh = value['refresh_token']
        print('::add-mask::' + next_refresh)
        save_refresh_token(next_refresh, repository, writer)
        access = value.get('access_token')
        expires = value.get('expires_in')
        if (not isinstance(access, str) or not access or '\n' in access or '\r' in access or
                type(expires) is not int or expires < 600 or value.get('token_type') != 'Bearer'):
            raise Failure('feishu_ci_member_token_invalid')
        print('::add-mask::' + access)
        with Path(os.environ['GITHUB_ENV']).open('a') as output:
            output.write('MOI_J5_FEISHU_ACCESS_TOKEN=' + access + '\n')
            output.write('MOI_J5_FEISHU_ACCESS_EXPIRES_AT=' + str(time.time() + expires) + '\n')
        print(json.dumps({'feishu_member_authorization': 'refreshed', 'successor_refresh_token': 'stored_in_qa_environment_secret',
                          'access_expires_in_seconds': expires}))
        return 0
    except Exception as error:
        print(json.dumps({'feishu_member_authorization': 'error',
                          'reason': error.code if isinstance(error, Failure) else type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
