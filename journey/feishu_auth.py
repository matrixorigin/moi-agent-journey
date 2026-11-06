"""Rotate CI member grants through encrypted repository state using the job token."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .client import Failure, NoRedirect
from .feishu import member_config
from .feishu_state import configured_store

REFRESH_URL = 'https://accounts.feishu.cn/oauth/v3/token'


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
    if (value.get('code') != 0 or not isinstance(value.get('refresh_token'), str) or not value['refresh_token'] or
            any(c in value['refresh_token'] for c in '\r\n')):
        raise Failure('feishu_member_refresh_failed', details={'code': value.get('code')})
    return value


def rotate(store, target, run_id):
    old, sha, _ = store.read()
    if old.get('status') != 'ready':
        raise Failure('feishu_previous_rotation_incomplete_or_unconfigured')
    refresh = old.get('refresh_token')
    generation = old.get('generation')
    if not isinstance(refresh, str) or not refresh or type(generation) is not int or generation < 0:
        raise Failure('feishu_state_grant_invalid')
    expires = old.get('refresh_expires_at')
    if not isinstance(expires, (int, float)) or expires <= time.time() + 60:
        raise Failure('feishu_refresh_grant_expired')
    print('::add-mask::' + refresh)
    # Commit an exclusive claim before consuming the one-use grant. A crash leaves an explicit
    # incomplete rotation, never permission for another run to spend the old token again.
    claim_sha = store.save({'status': 'refreshing', 'generation': generation,
        'run_id': run_id, 'claimed_at': time.time()}, sha)
    value = refresh_member(target['app_id'], os.environ['MOI_J5_FEISHU_APP_SECRET'], refresh)
    next_refresh = value['refresh_token']
    print('::add-mask::' + next_refresh)
    now = time.time()
    refresh_seconds = value.get('refresh_token_expires_in')
    successor = {'status': 'ready', 'generation': generation + 1, 'refresh_token': next_refresh,
        'refresh_expires_at': now + refresh_seconds if type(refresh_seconds) is int and refresh_seconds > 0 else 0}
    access = value.get('access_token')
    seconds = value.get('expires_in')
    usable = (isinstance(access, str) and access and '\n' not in access and '\r' not in access and
        type(seconds) is int and seconds >= 600 and value.get('token_type') == 'Bearer')
    if usable:
        print('::add-mask::' + access)
        successor.update(access_token=access, access_expires_at=now + seconds, access_run_id=run_id)
    # Save the successor even when its access grant is malformed, so it cannot be lost silently.
    store.save(successor, claim_sha)
    if not usable or successor['refresh_expires_at'] == 0:
        raise Failure('feishu_ci_member_token_invalid')
    return {'feishu_member_authorization': 'refreshed', 'generation': generation + 1,
        'successor_refresh_token': 'stored_as_authenticated_repository_ciphertext',
        'access_expires_in_seconds': seconds}


def export_access(store, run_id):
    value, _, _ = store.read()
    access, expiry = value.get('access_token'), value.get('access_expires_at')
    if (value.get('status') != 'ready' or value.get('access_run_id') != run_id or
            not isinstance(access, str) or not access or '\n' in access or '\r' in access or
            not isinstance(expiry, (int, float)) or expiry < time.time() + 600):
        raise Failure('feishu_current_run_access_grant_unavailable')
    print('::add-mask::' + access)
    with Path(os.environ['GITHUB_ENV']).open('a') as output:
        output.write('MOI_J5_FEISHU_ACCESS_TOKEN=' + access + '\n')
        output.write('MOI_J5_FEISHU_ACCESS_EXPIRES_AT=' + str(expiry) + '\n')
    return {'feishu_member_authorization': 'loaded_for_current_run'}


def main(command='rotate'):
    try:
        target = member_config()
        store = configured_store()
        run_id = os.environ['GITHUB_RUN_ID']
        if not run_id:
            raise Failure('feishu_current_run_id_missing')
        if command == 'rotate':
            result = rotate(store, target, run_id)
        elif command == 'access':
            result = export_access(store, run_id)
        else:
            raise Failure('feishu_auth_command_invalid')
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({'feishu_member_authorization': 'error',
                          'reason': error.code if isinstance(error, Failure) else type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) == 2 else 'rotate'))
