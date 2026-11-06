import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from journey.client import Failure
from journey.feishu_auth import export_access, rotate
from journey.feishu_state import RepositoryState, SMOKE_BRANCH, STATE_PATH


KEY = base64.b64encode(bytes(range(32))).decode()
READY = {'status': 'ready', 'generation': 0, 'refresh_token': 'private-old-refresh',
         'refresh_expires_at': 9_999_999_999}
SUCCESSOR = {'code': 0, 'refresh_token': 'private-new-refresh', 'access_token': 'private-access',
             'expires_in': 7200, 'refresh_token_expires_in': 604800, 'token_type': 'Bearer'}


class GitFixture:
    """Blob-level CAS and lost receipts, with genuine encryption around the boundary."""
    def __init__(self, store, value):
        self.raw = store.encrypt(value)
        self.calls, self.writes = [], []
        self.reject_write = 0
        self.drop_receipt = False

    @property
    def sha(self):
        return hashlib.sha1(self.raw).hexdigest()

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == 'GET':
            return {'type': 'file', 'path': STATE_PATH, 'encoding': 'base64', 'sha': self.sha,
                    'content': base64.b64encode(self.raw).decode()}
        self.writes.append(body)
        if self.reject_write == len(self.writes) or body['sha'] != self.sha:
            raise Failure('write_denied_or_conflict')
        self.raw = base64.b64decode(body['content'])
        if self.drop_receipt:
            self.drop_receipt = False
            raise Failure('receipt_lost_after_commit')
        return {'content': {'sha': self.sha}}


class StateTests(unittest.TestCase):
    def setUp(self):
        self.store = RepositoryState('owner/repo', 'app', KEY)
        self.git = GitFixture(self.store, copy.deepcopy(READY))
        self.store.api = self.git

    def test_ciphertext_does_not_contain_grant_and_uses_fresh_nonces(self):
        a, b = self.store.encrypt(READY), self.store.encrypt(READY)
        self.assertNotEqual(a, b)
        self.assertNotIn(READY['refresh_token'].encode(), a)
        self.assertEqual(self.store.decrypt(a), READY)

    def test_key_target_and_ciphertext_tampering_are_rejected(self):
        raw = self.store.encrypt(READY)
        wrong_key = base64.b64encode(b'x' * 32).decode()
        for other in [RepositoryState('owner/repo', 'app', wrong_key),
                      RepositoryState('owner/another', 'app', KEY),
                      RepositoryState('owner/repo', 'another-app', KEY),
                      RepositoryState('owner/repo', 'app', KEY, branch=SMOKE_BRANCH)]:
            with self.subTest(aad=other.aad), self.assertRaises(Failure):
                other.decrypt(raw)
        value = json.loads(raw)
        encrypted = bytearray(base64.b64decode(value['ciphertext']))
        encrypted[-1] ^= 1
        value['ciphertext'] = base64.b64encode(encrypted).decode()
        with self.assertRaises(Failure):
            self.store.decrypt(json.dumps(value).encode())

    def test_stale_writer_cannot_replace_a_newer_rotation(self):
        _, stale, _ = self.store.read()
        current = self.store.save({**READY, 'generation': 1}, stale)
        with self.assertRaises(Failure):
            self.store.save({**READY, 'generation': 2}, stale)
        observed, sha, _ = self.store.read()
        self.assertEqual(sha, current)
        self.assertEqual(observed['generation'], 1)

    def test_lost_write_receipt_is_resolved_without_repeating_the_write(self):
        _, sha, _ = self.store.read()
        self.git.drop_receipt = True
        result = self.store.save({**READY, 'generation': 1}, sha)
        self.assertEqual(result, self.git.sha)
        self.assertEqual(len(self.git.writes), 1)

    def test_wrong_key_and_unconfirmed_write_do_not_consume_refresh(self):
        self.git.reject_write = 1
        with patch.dict(os.environ, {'MOI_J5_FEISHU_APP_SECRET': 'private-app'}), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch('journey.feishu_auth.refresh_member') as refresh:
            with self.assertRaises(Failure):
                rotate(self.store, {'app_id': 'app'}, 'run-1')
        refresh.assert_not_called()
        self.assertEqual(self.store.read()[0], READY)

    def test_second_process_observes_claim_and_does_not_consume_same_grant(self):
        def during_exchange(*args):
            self.assertEqual(self.store.read()[0]['status'], 'refreshing')
            with self.assertRaises(Failure):
                rotate(self.store, {'app_id': 'app'}, 'competing-run')
            return SUCCESSOR
        with patch.dict(os.environ, {'MOI_J5_FEISHU_APP_SECRET': 'private-app'}), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch('journey.feishu_auth.refresh_member', side_effect=during_exchange) as refresh:
            rotate(self.store, {'app_id': 'app'}, 'run-1')
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(self.store.read()[0]['generation'], 1)

    def test_successor_is_committed_before_read_only_job_receives_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / 'env'
            env = {'MOI_J5_FEISHU_APP_SECRET': 'private-app', 'GITHUB_ENV': str(env_file)}
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()), \
                    patch('journey.feishu_auth.refresh_member', return_value=SUCCESSOR):
                rotate(self.store, {'app_id': 'app'}, 'run-1')
                self.assertFalse(env_file.exists())
                self.assertEqual(self.store.read()[0]['refresh_token'], SUCCESSOR['refresh_token'])
                export_access(self.store, 'run-1')
            self.assertIn('MOI_J5_FEISHU_ACCESS_TOKEN=private-access', env_file.read_text())
            self.assertNotIn('refresh', env_file.read_text())
        for body in self.git.writes:
            serialized = json.dumps(body)
            self.assertNotIn('private-access', serialized)
            self.assertNotIn('private-new-refresh', serialized)

    def test_failed_successor_write_leaves_incomplete_state_and_never_retries_exchange(self):
        self.git.reject_write = 2
        with patch.dict(os.environ, {'MOI_J5_FEISHU_APP_SECRET': 'private-app'}), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch('journey.feishu_auth.refresh_member', return_value=SUCCESSOR) as refresh:
            with self.assertRaises(Failure):
                rotate(self.store, {'app_id': 'app'}, 'run-1')
            with self.assertRaises(Failure):
                rotate(self.store, {'app_id': 'app'}, 'run-2')
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(self.store.read()[0]['status'], 'refreshing')

    def test_unknown_exchange_outcome_blocks_future_automatic_consumption(self):
        with patch.dict(os.environ, {'MOI_J5_FEISHU_APP_SECRET': 'private-app'}), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch('journey.feishu_auth.refresh_member', side_effect=Failure('outcome_unknown')) as refresh:
            for run in ('run-1', 'run-2'):
                with self.assertRaises(Failure):
                    rotate(self.store, {'app_id': 'app'}, run)
        self.assertEqual(refresh.call_count, 1)

    def test_malformed_access_still_preserves_new_refresh_grant(self):
        with patch.dict(os.environ, {'MOI_J5_FEISHU_APP_SECRET': 'private-app'}), \
                contextlib.redirect_stdout(io.StringIO()), \
                patch('journey.feishu_auth.refresh_member', return_value={**SUCCESSOR, 'token_type': 'DPoP'}):
            with self.assertRaises(Failure):
                rotate(self.store, {'app_id': 'app'}, 'run-1')
        value, _, _ = self.store.read()
        self.assertEqual(value['refresh_token'], SUCCESSOR['refresh_token'])
        self.assertNotIn('access_token', value)

    def test_current_run_and_remaining_lifetime_are_required(self):
        _, sha, _ = self.store.read()
        self.store.save({**READY, 'access_token': 'private-access', 'access_run_id': 'run-1',
                         'access_expires_at': time.time() + 7200}, sha)
        with self.assertRaises(Failure):
            export_access(self.store, 'different-run')
        value, sha, _ = self.store.read()
        self.store.save({**value, 'access_expires_at': time.time() + 10}, sha)
        with self.assertRaises(Failure):
            export_access(self.store, 'run-1')

    def test_encrypted_body_uses_stdin_and_github_job_token_is_not_in_argv(self):
        store = RepositoryState('owner/repo', 'app', KEY)
        body = {'content': base64.b64encode(store.encrypt(READY)).decode()}
        with patch('journey.feishu_state.subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout='{}')) as call:
            store.api('PUT', store.path, body)
        args = call.call_args.args[0]
        self.assertEqual(args[-2:], ['--input', '-'])
        self.assertNotIn('private-old-refresh', json.dumps(call.call_args.kwargs))
