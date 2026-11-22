"""First-boot / reset admin passwords must always satisfy the password policy.

Regression: ``src.core.admin.auth.helpers._generate_password`` used to draw 24
characters independently from ``ascii_letters + digits + "!@#$%^&*"``. Roughly
8% of draws lacked a digit or a special character, so the bootstrap admin
credential intermittently failed the same character-class policy that
registration, password change/reset and admin ``set_password`` enforce. It now
delegates to the shared policy-compliant generator, which guarantees every
required class while preserving CSPRNG strength.

Non-vacuity: the control test re-creates the old draw *inside the test* and
asserts that the real validators actually reject some of those draws. A test
suite that had silently dropped the character-class checks would fail here.
"""

import secrets
import string

import pytest

from src.core.admin.auth.auth_mixin import AuthenticationMixin
from src.core.admin.auth.helpers import _generate_password
from src.core.admin.auth.password_mixin import PasswordMixin
from src.core.auth.passwords import validate_password

pytestmark = [pytest.mark.security, pytest.mark.unit]

_DRAWS = 1500
_LEGACY_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"


def _legacy_style_draw(length: int = 24) -> str:
    """The old generator, kept only as a failing control for non-vacuity."""
    return "".join(secrets.choice(_LEGACY_ALPHABET) for _ in range(length))


def test_policy_checks_are_real_legacy_draw_intermittently_fails():
    admin_policy = PasswordMixin()._validate_password_policy
    shared_failures = 0
    admin_failures = 0
    for _ in range(_DRAWS):
        pw = _legacy_style_draw()
        shared_failures += not validate_password(pw).valid
        admin_failures += not admin_policy(pw)[0]
    # Both policies are character-class based; the old draw must be caught by
    # them often enough that a zero count would mean the checks are vacuous.
    assert shared_failures > 0
    assert admin_failures > 0


def test_generated_admin_password_always_satisfies_shared_policy():
    for _ in range(_DRAWS):
        pw = _generate_password()
        result = validate_password(pw)
        assert result.valid, (pw, result.issues)


def test_generated_admin_password_always_satisfies_admin_policy():
    admin_policy = PasswordMixin()._validate_password_policy
    for _ in range(_DRAWS):
        pw = _generate_password()
        ok, message = admin_policy(pw)
        assert ok, (pw, message)


def test_generated_admin_password_preserves_csprng_strength():
    passwords = {_generate_password() for _ in range(100)}
    assert len(passwords) == 100  # every draw is unique
    # token_urlsafe(32) is 43 chars; the fix only appends, never shortens.
    assert all(len(pw) >= 43 for pw in passwords)


class _FakeDB:
    """Minimal DB double: enough for ``ensure_admin_user`` to run."""

    def __init__(self, existing_admin: bool = False):
        self.existing_admin = existing_admin
        self.executes = []

    def fetch_one(self, sql, params=()):
        statement = " ".join(sql.split())
        if "FROM admin_users WHERE username" in statement:
            return {"id": 1} if self.existing_admin else None
        if "COUNT(*) as c FROM admin_roles" in statement:
            return {"c": 1}
        if "FROM admin_roles WHERE name" in statement:
            return {"id": 7}
        if "SELECT 1 AS assigned" in statement:
            return {"assigned": 1}
        return None

    def execute(self, sql, params=()):
        self.executes.append((" ".join(sql.split()), params))


@pytest.mark.parametrize("force", [False, True])
def test_ensure_admin_user_installs_policy_compliant_credential(monkeypatch, force):
    import src.core.admin.auth.auth_mixin as auth_mixin

    captured = []
    monkeypatch.setattr(auth_mixin, "_save_admin_credentials", captured.append)

    db = _FakeDB(existing_admin=force)
    mixin = AuthenticationMixin()
    mixin._db = db
    mixin.ensure_admin_user(force=force)

    assert len(captured) == 1
    password = captured[0]
    assert validate_password(password).valid
    assert PasswordMixin()._validate_password_policy(password)[0]
    # The credential handed to the operator is the one actually persisted.
    inserts = [
        params
        for statement, params in db.executes
        if statement.startswith("INSERT INTO admin_users")
    ]
    assert len(inserts) == 1
    assert inserts[0][2]  # non-empty password hash
