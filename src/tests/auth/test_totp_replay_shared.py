"""Regression tests for cross-worker TOTP replay protection.

Without a shared cache backend the replay cache used to fall back to a
process-local dict, so the same TOTP code presented to two workers inside its
validity window was accepted twice (2FA replay). The cache now claims each
code in the shared auth database with an atomic insert-or-ignore, so a second
worker using the same database sees the replay even when Valkey is absent.
"""

import time
from unittest.mock import patch

from src.core.auth.totp import TOTPReplayCache, set_replay_cache_db


def _no_shared_cache():
    """Force the shared (Valkey) claim path to report 'unavailable'."""
    return patch("src.core.database.valkey_client.get_client", return_value=None)


class TestTOTPReplaySharedDatabase:
    def test_replay_blocked_across_cache_instances(self, db):
        """Two caches on one DB (two workers) cannot both claim a code."""
        worker_a = TOTPReplayCache(ttl_seconds=90)
        worker_b = TOTPReplayCache(ttl_seconds=90)
        worker_a.set_db(db)
        worker_b.set_db(db)
        with _no_shared_cache():
            assert worker_a.check_and_mark(7, "123456", purpose="login") is True
            # Same code, second worker, same shared DB -> replay.
            assert worker_b.check_and_mark(7, "123456", purpose="login") is False

    def test_scope_isolation_is_preserved(self, db):
        """Different purposes claim independently on the DB fallback."""
        worker_a = TOTPReplayCache(ttl_seconds=90)
        worker_b = TOTPReplayCache(ttl_seconds=90)
        worker_a.set_db(db)
        worker_b.set_db(db)
        with _no_shared_cache():
            assert worker_a.check_and_mark(8, "654321", purpose="login") is True
            assert worker_b.check_and_mark(8, "654321", purpose="disable_2fa") is True

    def test_expired_claim_is_reusable(self, db):
        """A claim whose window elapsed does not block a later use."""
        cache = TOTPReplayCache(ttl_seconds=1)
        cache.set_db(db)
        with _no_shared_cache():
            assert cache.check_and_mark(9, "111111", purpose="login") is True
            assert cache.check_and_mark(9, "111111", purpose="login") is False
            time.sleep(1.1)
            assert cache.check_and_mark(9, "111111", purpose="login") is True

    def test_falls_back_when_db_lacks_claim_support(self):
        """A DB without a working claim path degrades to the local cache."""

        class _NoClaimDb:
            def convert_schema(self, sql):
                return sql

            def execute(self, *args, **kwargs):
                raise RuntimeError("no usable db")

        cache = TOTPReplayCache(ttl_seconds=90)
        cache.set_db(_NoClaimDb())
        with _no_shared_cache():
            assert cache.check_and_mark(3, "222222", purpose="login") is True
            assert cache.check_and_mark(3, "222222", purpose="login") is False

    def test_global_cache_accepts_db_wiring(self, db):
        """The auth manager wiring path attaches the shared DB."""
        set_replay_cache_db(db)
        from src.core.auth import totp as totp_module

        with _no_shared_cache():
            assert (
                totp_module._replay_cache.check_and_mark(42, "333333", purpose="login")
                is True
            )


class TestTOTPReplaySharedValkey:
    """The Valkey claim must be conditional (SET NX).

    A plain overwrite-SET always reports success, so the second worker
    would accept the replay. The cache must use the single-winner
    ``set_nx`` claim instead.
    """

    def test_replay_blocked_across_workers_on_shared_cache(self):
        claimed_keys: dict = {}

        class _FakeValkey:
            def set_nx(self, key, value, ttl=None):
                if key in claimed_keys:
                    return False
                claimed_keys[key] = value
                return True

        worker_a = TOTPReplayCache(ttl_seconds=90)
        worker_b = TOTPReplayCache(ttl_seconds=90)
        with (
            patch(
                "src.core.database.valkey_client.get_client",
                return_value=_FakeValkey(),
            ),
            patch("src.core.database.valkey_client.is_available", return_value=True),
        ):
            assert worker_a.check_and_mark(7, "123456", purpose="login") is True
            # Same code, second worker, same shared cache -> replay.
            assert worker_b.check_and_mark(7, "123456", purpose="login") is False

    def test_scope_isolation_on_shared_cache(self):
        claimed_keys: dict = {}

        class _FakeValkey:
            def set_nx(self, key, value, ttl=None):
                if key in claimed_keys:
                    return False
                claimed_keys[key] = value
                return True

        worker_a = TOTPReplayCache(ttl_seconds=90)
        worker_b = TOTPReplayCache(ttl_seconds=90)
        with (
            patch(
                "src.core.database.valkey_client.get_client",
                return_value=_FakeValkey(),
            ),
            patch("src.core.database.valkey_client.is_available", return_value=True),
        ):
            assert worker_a.check_and_mark(8, "654321", purpose="login") is True
            assert worker_b.check_and_mark(8, "654321", purpose="disable_2fa") is True


class TestVerifyTotpPurposeIsolation:
    """End-to-end through verify_totp_code: one code, two purposes.

    ``complete_2fa`` (purpose="login") and ``schedule_account_deletion``
    (purpose="delete_account") used to share the bare ``user_id`` replay
    scope, so completing a login consumed the code for a deletion (and vice
    versa). Distinct purposes must claim independently; the same purpose
    must still block the replay.
    """

    def _isolated_global_cache(self, totp_module):
        """Detach shared backends so the global cache uses its local dict."""
        cache = totp_module._replay_cache
        saved = (
            dict(cache._used_codes),
            cache._db,
            cache._db_table_ready,
        )
        cache._used_codes.clear()
        cache._db = None
        cache._db_table_ready = False
        return cache, saved

    def test_login_and_delete_account_scopes_are_isolated(self):
        from src.core.auth import totp as totp_module

        cache, saved = self._isolated_global_cache(totp_module)
        try:
            with _no_shared_cache():
                secret = totp_module.generate_totp_secret()
                user_id = 987654321
                code = totp_module.get_current_totp_code(secret)
                assert (
                    totp_module.verify_totp_code(
                        secret,
                        code,
                        user_id=user_id,
                        purpose="login",
                        acting_user_id=user_id,
                    )
                    is True
                )
                # Same code, different privileged purpose -> accepted.
                assert (
                    totp_module.verify_totp_code(
                        secret,
                        code,
                        user_id=user_id,
                        purpose="delete_account",
                        acting_user_id=user_id,
                    )
                    is True
                )
        finally:
            cache._used_codes.clear()
            cache._used_codes.update(saved[0])
            cache._db = saved[1]
            cache._db_table_ready = saved[2]

    def test_same_purpose_replay_is_still_blocked(self):
        from src.core.auth import totp as totp_module

        cache, saved = self._isolated_global_cache(totp_module)
        try:
            with _no_shared_cache():
                secret = totp_module.generate_totp_secret()
                user_id = 987654322
                code = totp_module.get_current_totp_code(secret)
                assert (
                    totp_module.verify_totp_code(
                        secret,
                        code,
                        user_id=user_id,
                        purpose="login",
                        acting_user_id=user_id,
                    )
                    is True
                )
                assert (
                    totp_module.verify_totp_code(
                        secret,
                        code,
                        user_id=user_id,
                        purpose="login",
                        acting_user_id=user_id,
                    )
                    is False
                )
        finally:
            cache._used_codes.clear()
            cache._used_codes.update(saved[0])
            cache._db = saved[1]
            cache._db_table_ready = saved[2]
