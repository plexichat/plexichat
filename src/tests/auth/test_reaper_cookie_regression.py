"""Agent5 C2/H5 regression: deletion-reaper ms math + secure-cookie default.

C2: scheduled deletions are stored/compared in ms-epoch; a past ``deletion_at``
must purge on harvest, a future one must survive. The admin delay path accepts
seconds-epoch values and must normalize them to ms (storing seconds raw would
schedule the deadline in 1970 and purge on the next cycle).
H5: the session cookie must be Secure-by-default (only an explicit
``session_cookie_secure: false`` relaxes it) with HttpOnly + SameSite=Lax on
both set and clear paths.
"""

import time
import uuid

import pytest

from src.api.routes.auth import cookies as cookie_mod
from src.core.auth.deletion_log import DeletionLog
from src.core.auth.reaper import AccountReaper


def _register(auth_manager, label="reapreg"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    return auth_manager.register(
        username=username, email=email, password="TestPass123!"
    )


def _reaper(db, auth_manager, tmp_path):
    reaper = AccountReaper(db, auth_manager)
    reaper._log = DeletionLog(file_path=str(tmp_path / "deletion_log.jsonl"))
    return reaper


class TestReaperMsMath:
    def test_harvest_purges_past_deletion_at(self, db, auth_manager, tmp_path):
        user = _register(auth_manager, "reapast")
        assert auth_manager.schedule_account_deletion(user.id, "TestPass123!") is True
        row = db.fetch_one(
            "SELECT deletion_status, deletion_at FROM auth_users WHERE id = ?",
            (user.id,),
        )
        assert row["deletion_status"] == "frozen"
        # Scheduling stores a ms-epoch deadline in the future.
        assert row["deletion_at"] > int(time.time() * 1000) > 100_000_000_000

        db.execute(
            "UPDATE auth_users SET deletion_at = ? WHERE id = ?",
            (int(time.time() * 1000) - 1000, user.id),
        )
        _reaper(db, auth_manager, tmp_path).harvest()

        assert (
            db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user.id,)) is None
        )

    def test_harvest_spares_future_deletion_at(self, db, auth_manager, tmp_path):
        user = _register(auth_manager, "reafut")
        assert auth_manager.schedule_account_deletion(user.id, "TestPass123!") is True
        db.execute(
            "UPDATE auth_users SET deletion_at = ? WHERE id = ?",
            (int(time.time() * 1000) + 30 * 86400 * 1000, user.id),
        )
        _reaper(db, auth_manager, tmp_path).harvest()

        row = db.fetch_one(
            "SELECT deletion_status FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row is not None and row["deletion_status"] == "frozen"

    def test_delay_seconds_epoch_normalized_to_ms(self, db, auth_manager, tmp_path):
        user = _register(auth_manager, "readelay")
        assert auth_manager.schedule_account_deletion(user.id, "TestPass123!") is True
        manager = auth_manager._get_manager()

        # The admin route validates/passes seconds-epoch; the core must store ms.
        delay_seconds = int(time.time()) + 60 * 86400
        assert manager.delay_account_deletion(user.id, delay_seconds) is True
        row = db.fetch_one(
            "SELECT deletion_at FROM auth_users WHERE id = ?", (user.id,)
        )
        assert row["deletion_at"] == delay_seconds * 1000

        # A normalized (future, ms) deadline must NOT purge on harvest.
        _reaper(db, auth_manager, tmp_path).harvest()
        assert (
            db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user.id,))
            is not None
        )

    def test_delay_rejects_past_deadline(self, db, auth_manager):
        user = _register(auth_manager, "reapast2")
        assert auth_manager.schedule_account_deletion(user.id, "TestPass123!") is True
        manager = auth_manager._get_manager()
        with pytest.raises(ValueError):
            manager.delay_account_deletion(user.id, int(time.time()) - 60)
        with pytest.raises(ValueError):
            manager.delay_account_deletion(user.id, int(time.time() * 1000) - 60)

    def test_rollback_prefers_metadata_ms_deadline(self, db, auth_manager, tmp_path):
        user = _register(auth_manager, "rearoll")
        reaper = _reaper(db, auth_manager, tmp_path)
        deadline_ms = int(time.time() * 1000) + 45 * 86400 * 1000
        reaper._log.log_event(
            user.id,
            "SCHEDULED",
            user.username,
            {"scheduled_at": int(time.time() * 1000), "deletion_at": deadline_ms},
        )
        reaper.perform_rollback_protection_check()

        row = db.fetch_one(
            "SELECT deletion_status, deletion_at FROM auth_users WHERE id = ?",
            (user.id,),
        )
        assert row["deletion_status"] == "frozen"
        assert row["deletion_at"] == deadline_ms

    def test_rollback_honors_delayed_deadline(self, db, auth_manager, tmp_path):
        user = _register(auth_manager, "readel")
        reaper = _reaper(db, auth_manager, tmp_path)
        reaper._log.log_event(
            user.id,
            "SCHEDULED",
            user.username,
            {
                "scheduled_at": int(time.time() * 1000),
                "deletion_at": int(time.time() * 1000) + 30 * 86400 * 1000,
            },
        )
        delayed_ms = int(time.time() * 1000) + 90 * 86400 * 1000
        reaper._log.log_event(
            user.id, "DELAYED", user.username, {"deletion_at": delayed_ms}
        )
        reaper.perform_rollback_protection_check()

        row = db.fetch_one(
            "SELECT deletion_status, deletion_at FROM auth_users WHERE id = ?",
            (user.id,),
        )
        assert row["deletion_status"] == "frozen"
        assert row["deletion_at"] == delayed_ms

    def test_rollback_deletion_at_seconds_normalized(self):
        past_s = int(time.time()) - 10
        resolved = AccountReaper._rollback_deletion_at(
            {"timestamp": past_s, "metadata": {}}, past_s, 30
        )
        assert resolved == past_s * 1000 + 30 * 86400 * 1000
        explicit_ms = int(time.time() * 1000) + 1000
        assert (
            AccountReaper._rollback_deletion_at(
                {"timestamp": past_s, "metadata": {"deletion_at": explicit_ms}},
                past_s,
                30,
            )
            == explicit_ms
        )


class TestDeletionLogTransitions:
    def test_force_purged_evicts_scheduled(self, tmp_path):
        log = DeletionLog(file_path=str(tmp_path / "t.jsonl"))
        log.log_event(7, "SCHEDULED", "u7", {})
        assert 7 in log.get_scheduled_deletions()
        log.log_event(7, "FORCE_PURGED", "u7", {})
        assert 7 not in log.get_scheduled_deletions()

    def test_cancelled_and_purged_evict_scheduled(self, tmp_path):
        log = DeletionLog(file_path=str(tmp_path / "t.jsonl"))
        log.log_event(8, "SCHEDULED", "u8", {})
        log.log_event(8, "CANCELLED", "u8", {})
        assert 8 not in log.get_scheduled_deletions()
        log.log_event(9, "SCHEDULED", "u9", {})
        log.log_event(9, "PURGED", "u9", {})
        assert 9 not in log.get_scheduled_deletions()


class TestSecureCookieDefault:
    def test_secure_defaults_true_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(cookie_mod, "_auth_config", lambda: {})
        assert cookie_mod.session_cookie_secure() is True
        monkeypatch.setattr(cookie_mod, "_auth_config", lambda: {"security": {}})
        assert cookie_mod.session_cookie_secure() is True

    def test_explicit_false_relaxes_only_with_dev_flag(self, monkeypatch):
        monkeypatch.setattr(
            cookie_mod,
            "_auth_config",
            lambda: {"security": {"session_cookie_secure": False}},
        )
        assert cookie_mod.session_cookie_secure() is False
        monkeypatch.setattr(
            cookie_mod,
            "_auth_config",
            lambda: {"security": {"session_cookie_secure": True}},
        )
        assert cookie_mod.session_cookie_secure() is True

    def test_set_and_clear_carry_secure_httponly_samesite(self, monkeypatch):
        from fastapi import Response

        monkeypatch.setattr(
            cookie_mod,
            "_auth_config",
            lambda: {"security": {"session_cookie_secure": True}},
        )
        resp = Response()
        cookie_mod.set_session_cookie(resp, "tok123")
        set_cookie = resp.headers["set-cookie"].lower()
        assert "secure" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=lax" in set_cookie

        cleared = Response()
        cookie_mod.clear_session_cookie(cleared)
        clear_cookie = cleared.headers["set-cookie"].lower()
        assert "httponly" in clear_cookie
        assert "samesite=lax" in clear_cookie
        assert "secure" in clear_cookie

    def test_dev_relaxation_applies_to_both_paths(self, monkeypatch):
        from fastapi import Response

        monkeypatch.setattr(
            cookie_mod,
            "_auth_config",
            lambda: {"security": {"session_cookie_secure": False}},
        )
        resp = Response()
        cookie_mod.set_session_cookie(resp, "tok123")
        assert "secure" not in resp.headers["set-cookie"].lower()
        cleared = Response()
        cookie_mod.clear_session_cookie(cleared)
        assert "secure" not in cleared.headers["set-cookie"].lower()

    def test_production_config_default_is_secure(self, tmp_path):
        from src.config_defaults_authentication import get_authentication_defaults

        defaults = get_authentication_defaults(tmp_path)
        assert defaults["security"]["session_cookie_secure"] is True
