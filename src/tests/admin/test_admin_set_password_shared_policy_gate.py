"""Admin-set passwords must satisfy the shared password policy.

``PasswordMixin.set_password`` backs ``PATCH /admin/users/{id}``. It validated
only against ``admin_ui.security.password_policy``, which has no maximum
length check and a 12-entry local common-password denylist. Every other
credential-setting path (registration, self-service change, token reset) goes
through ``src.core.auth.passwords.validate_password``: 128-char configured /
1024-char absolute ceiling and the bundled 10,000-entry breached-password list.
An admin could therefore install a credential the rest of the system rejects.
"""

import pytest

import utils.config as config
from src.core.admin.auth.password_mixin import PasswordMixin
from src.core.auth.passwords import validate_password

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _Cursor:
    rowcount = 1


class _FakeDB:
    def __init__(self, username="admin"):
        self.username = username
        self.executed = []

    def fetch_one(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM admin_users WHERE id" in normalized:
            return {"username": self.username}
        if "FROM auth_users WHERE username" in normalized:
            return {"id": 7}
        return None

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor()


def _mixin(monkeypatch):
    monkeypatch.setattr("src.core.database.invalidate_pattern", lambda _p: None)
    monkeypatch.setattr("src.utils.encryption.hash_password", lambda _pw: "hashed")
    db = _FakeDB()
    mixin = PasswordMixin()
    mixin._db = db
    return mixin, db


def _writes(db):
    return [(sql, params) for sql, params in db.executed if sql.startswith("UPDATE")]


def test_admin_set_password_enforces_shared_length_ceiling(monkeypatch):
    mixin, db = _mixin(monkeypatch)
    # Passes every admin composition rule but is far above the 128-char
    # ceiling the shared validator applies to registration/change/reset.
    long_password = "Aa1!" + "x" * 200

    control = validate_password(long_password)
    assert control.valid is False, "shared validator must reject the long password"

    ok, message = mixin.set_password(1, long_password)
    assert ok is False, f"admin set_password accepted an over-long password: {message}"
    assert _writes(db) == [], "no credential write may occur for a rejected password"


def test_admin_set_password_enforces_shared_breached_password_list(monkeypatch):
    # Operator relaxes composition (NIST-style) so the breached-password list
    # is the real control. The admin policy's 12-entry denylist does not know
    # this breached password; the shared 10,000-entry list does.
    admin_cfg = {
        "security": {
            "password_policy": {
                "min_length": 12,
                "require_uppercase": False,
                "require_lowercase": False,
                "require_numbers": False,
                "require_special_chars": False,
                "prevent_common_passwords": True,
            }
        }
    }
    real_get = config.get

    def fake_get(key, default=None):
        if key == "admin_ui":
            return admin_cfg
        return real_get(key, default)

    monkeypatch.setattr("src.core.admin.auth.password_mixin.config.get", fake_get)
    mixin, db = _mixin(monkeypatch)
    breached = "unbelievable"  # 12 chars, present in common-passwords.txt

    control = validate_password(breached)
    assert control.valid is False, "shared validator must reject a breached password"

    ok, message = mixin.set_password(1, breached)
    assert ok is False, f"admin set_password accepted a breached password: {message}"
    assert _writes(db) == [], "no credential write may occur for a rejected password"


def test_admin_set_password_still_accepts_strong_password(monkeypatch):
    """Non-vacuous control: a genuinely strong password is not blocked."""
    mixin, db = _mixin(monkeypatch)
    ok, message = mixin.set_password(1, "Cf7!vL2qP9xR4zM8bK3t")
    assert ok is True, message
    assert any("SET password_hash" in sql for sql, _ in _writes(db))
