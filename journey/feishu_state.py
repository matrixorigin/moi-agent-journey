"""Authenticated ciphertext in a dedicated branch; GitHub receives no plaintext grant."""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .client import Failure, NoRedirect
from .feishu import member_config

STATE_BRANCH = 'ci/j5-feishu-state'
SMOKE_BRANCH = 'ci/j5-feishu-state-smoke'
STATE_PATH = 'feishu-member.enc.json'


class RepositoryState:
    def __init__(self, repository, app_id, key, *, branch=STATE_BRANCH):
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or
                branch not in {STATE_BRANCH, SMOKE_BRANCH} or not app_id):
            raise Failure('feishu_state_invalid_target')
        try:
            raw = base64.b64decode(key, validate=True)
            if len(raw) != 32:
                raise ValueError()
        except (ValueError, TypeError):
            raise Failure('feishu_state_invalid_key') from None
        self.repository, self.branch = repository, branch
        self.cipher = AESGCM(raw)
        self.aad = json.dumps([1, repository, branch, STATE_PATH, app_id], separators=(',', ':')).encode()
        self.path = f'repos/{repository}/contents/{STATE_PATH}'

    def api(self, method, path, body=None):
        # gh takes the job's GH_TOKEN from env; neither credentials nor ciphertext enter argv.
        args = ['gh', 'api', '--method', method, path]
        if body is not None:
            args += ['--input', '-']
        try:
            result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure('feishu_state_request_outcome_unknown') from None
        if result.returncode:
            raise Failure('feishu_state_github_request_failed')
        try:
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise Failure('feishu_state_github_invalid_response') from None

    def encrypt(self, value):
        raw = json.dumps(value, separators=(',', ':')).encode()
        if len(raw) > 32_000:
            raise Failure('feishu_state_too_large')
        nonce = os.urandom(12)
        return json.dumps({'version': 1, 'algorithm': 'AES-256-GCM',
            'nonce': base64.b64encode(nonce).decode(),
            'ciphertext': base64.b64encode(self.cipher.encrypt(nonce, raw, self.aad)).decode()},
            separators=(',', ':')).encode()

    def decrypt(self, raw):
        try:
            if len(raw) > 50_000:
                raise ValueError()
            value = json.loads(raw)
            if (not isinstance(value, dict) or value.get('version') != 1 or
                    value.get('algorithm') != 'AES-256-GCM'):
                raise ValueError()
            nonce = base64.b64decode(value['nonce'], validate=True)
            if len(nonce) != 12:
                raise ValueError()
            plain = self.cipher.decrypt(nonce, base64.b64decode(value['ciphertext'], validate=True), self.aad)
            state = json.loads(plain)
            if not isinstance(state, dict):
                raise ValueError()
            return state
        except (ValueError, KeyError, TypeError, InvalidTag):
            raise Failure('feishu_state_authentication_failed') from None

    def read(self):
        result = self.api('GET', self.path + '?ref=' + self.branch)
        try:
            if (result['type'] != 'file' or result['path'] != STATE_PATH or
                    result['encoding'] != 'base64' or not re.fullmatch(r'[0-9a-f]{40}', result['sha'])):
                raise ValueError()
            encoded = base64.b64decode(''.join(result['content'].split()), validate=True)
        except (KeyError, ValueError, TypeError):
            raise Failure('feishu_state_invalid_file') from None
        return self.decrypt(encoded), result['sha'], encoded

    def save(self, value, expected_sha):
        """CAS on the exact current blob; never rebase or overwrite a competing rotation."""
        encoded = self.encrypt(value)
        try:
            result = self.api('PUT', self.path, {'branch': self.branch, 'sha': expected_sha,
                'message': 'Update encrypted Feishu credential state',
                'content': base64.b64encode(encoded).decode()})
            sha = result.get('content', {}).get('sha', '')
            if not re.fullmatch(r'[0-9a-f]{40}', sha):
                raise Failure('feishu_state_invalid_write_receipt')
            return sha
        except Failure:
            # A lost write receipt can be resolved read-only. Never repeat a mutation or OAuth exchange.
            try:
                _, actual_sha, actual = self.read()
                if actual == encoded:
                    return actual_sha
            except Failure:
                pass
            raise Failure('feishu_state_write_not_confirmed') from None

    def initialize(self, value):
        """Explicit provisioning only. Create an orphan branch containing ciphertext alone."""
        encoded = self.encrypt(value)
        prefix = f'repos/{self.repository}/git/'
        tree = self.api('POST', prefix + 'trees', {'tree': [{'path': STATE_PATH,
            'mode': '100644', 'type': 'blob', 'content': encoded.decode()}]})
        commit = self.api('POST', prefix + 'commits', {'message': 'Initialize encrypted Feishu state',
            'tree': tree['sha'], 'parents': []})
        self.api('POST', prefix + 'refs', {'ref': 'refs/heads/' + self.branch, 'sha': commit['sha']})
        return self.read()


def configured_store(*, branch=STATE_BRANCH):
    target = member_config()
    return RepositoryState(os.environ['GITHUB_REPOSITORY'], target['app_id'],
        os.environ['MOI_J5_FEISHU_STATE_KEY'], branch=branch)


def seed(grant_file, expected_sha=None):
    """One-time local provisioning using the operator's existing repository access."""
    target, store = member_config(), configured_store()
    value = json.loads(Path(grant_file).read_text())
    required = {'im:message', 'im:message.send_as_user', 'im:message.group_msg:get_as_user', 'offline_access'}
    token, refresh = value.get('access_token'), value.get('refresh_token')
    if (value.get('token_type') != 'Bearer' or not isinstance(token, str) or not token or
            not isinstance(refresh, str) or not refresh or any(c in token + refresh for c in '\r\n') or
            not required.issubset(set(value.get('scope', '').split()))):
        raise Failure('feishu_seed_grant_invalid')
    received, refresh_seconds = value.get('received_at'), value.get('refresh_token_expires_in')
    if (not isinstance(received, (int, float)) or type(refresh_seconds) is not int or
            received + refresh_seconds <= time.time() + 600):
        raise Failure('feishu_seed_grant_expired_or_missing_lifetime')
    request = urllib.request.Request('https://open.feishu.cn/open-apis/authen/v1/user_info',
        headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            identity = json.loads(response.read(100_001))
    except Exception:
        raise Failure('feishu_seed_member_identity_unavailable') from None
    if identity.get('code') != 0 or identity.get('data', {}).get('open_id') != target['user_open_id']:
        raise Failure('feishu_seed_member_identity_mismatch')
    state = {'status': 'ready', 'generation': 0, 'refresh_token': refresh,
        'refresh_expires_at': received + refresh_seconds}
    if expected_sha is None:
        store.initialize(state)
    else:
        # Recovery requires an explicit blob SHA and fresh member authorization. No reset on reads.
        if not re.fullmatch(r'[0-9a-f]{40}', expected_sha):
            raise Failure('feishu_seed_recovery_sha_invalid')
        store.save(state, expected_sha)
    print(json.dumps({'feishu_seed': 'stored_as_authenticated_repository_ciphertext',
        'member_identity': 'matches_configured_test_member'}))


def smoke():
    """Real GITHUB_TOKEN reads/writes, random in-memory key and synthetic grants; no QA/Feishu."""
    key = base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()
    store = RepositoryState(os.environ['GITHUB_REPOSITORY'], 'ci-synthetic-only', key, branch=SMOKE_BRANCH)
    # A fixed smoke branch already exists after its first run. Its current blob supplies the CAS.
    result = store.api('GET', store.path + '?ref=' + SMOKE_BRANCH)
    stale = result['sha']
    fake = {'status': 'smoke', 'synthetic_token': 'CI_TEST_ONLY_' + uuid.uuid4().hex}
    current = store.save(fake, stale)
    observed, sha, raw = store.read()
    if observed != fake or sha != current or fake['synthetic_token'].encode() in raw:
        raise Failure('feishu_state_smoke_roundtrip_failed')
    newer = store.save({**fake, 'generation': 2}, current)
    try:
        store.save({**fake, 'generation': 3}, stale)
    except Failure:
        pass
    else:
        raise Failure('feishu_state_smoke_conflict_not_rejected')
    final, sha, _ = store.read()
    if final.get('generation') != 2 or sha != newer:
        raise Failure('feishu_state_smoke_conflict_overwrote_state')
    print(json.dumps({'encrypted_state_roundtrip': 'passed', 'stale_write': 'rejected',
        'credential': 'automatic_repository_GITHUB_TOKEN', 'real_feishu_credentials_used': False}))


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['smoke']:
            smoke()
        elif len(sys.argv) in {3, 4} and sys.argv[1] == 'seed':
            seed(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)
        else:
            raise Failure('feishu_state_command_invalid')
    except Exception as error:
        print(json.dumps({'encrypted_state': 'error',
            'reason': error.code if isinstance(error, Failure) else type(error).__name__}))
        raise SystemExit(1)
