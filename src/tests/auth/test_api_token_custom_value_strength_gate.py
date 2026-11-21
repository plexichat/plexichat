"""Caller-supplied API access tokens must meet the generated-token strength bar.

``create_api_access_token`` accepts an optional ``token_value``. When it is
absent the manager mints ``secrets.token_urlsafe(24)`` (32 urlsafe chars, 192
bits). When it is present the old code stripped it and wrote it straight into
``auth_api_access_tokens`` as a permanent bearer credential with no strength or
length check at all -- unlike every other credential-setting path, which
validates before hashing/persisting. The admin route's "caller values are
rejected" guard protects only one of the manager's callers; the rotation helper
and any internal caller that forwards request data bypass it and can install a
one-character, pre-known API key.

This pins the manager-level gate so the credential itself is always bounded.
"""

from types import SimpleNamespace

import pytest

from src.core.auth.managers.api_token import ApiTokenMixin
from src.core.auth.tokens import MAX_TOKEN_LENGTH

pytestmark = [pytest.mark.security, pytest.mark.auth]

# Match the entropy of the server-generated default, secrets.token_urlsafe(24).
EXPECTED_MIN = 32


class _Cursor:
    rowcount = 1


class _FakeDB:
    def __init__(self):
        self.executed = []

    def fetch_one(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM auth_api_access_tokens WHERE token_index" in normalized:
            return None  # no existing token with this index
        if "FROM auth_api_access_tokens WHERE id" in normalized:
            return {"id": 1}
        return None

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), tuple(params)))
        return _Cursor()


class _FakeCrypto:
    def fast_blind_index(self, token, context):
        return "idx:" + token

    def encrypt_data(self, token, context=None):
        return "enc:" + token


def _mixin(monkeypatch):
    monkeypatch.setattr(
        "src.core.auth.managers.api_token.invalidate_pattern", lambda _p: None
    )
    monkeypatch.setattr(
        "src.core.auth.managers.api_token._invalidate_api_token_verify_cache",
        lambda: None,
    )
    db = _FakeDB()
    mixin = ApiTokenMixin()
    mixin._db = db
    mixin.crypto = _FakeCrypto()
    mixin._generate_id = lambda: 1
    mixin._get_timestamp = lambda: 1_000_000
    mixin._row_to_access_token = lambda row: SimpleNamespace()
    return mixin, db


def _writes(db):
    return [sql for sql, _ in db.executed if sql.startswith("INSERT")]


def test_short_custom_api_token_is_rejected_without_persisting(monkeypatch):
    mixin, db = _mixin(monkeypatch)
    weak = "a"

    with pytest.raises(ValueError, match="must be between"):
        mixin.create_api_access_token("weak", created_by=1, token_value=weak)

    assert _writes(db) == [], "no credential write may occur for a rejected token"


def test_overlong_custom_api_token_is_rejected(monkeypatch):
    mixin, db = _mixin(monkeypatch)
    huge = "A" * (MAX_TOKEN_LENGTH + 1)

    with pytest.raises(ValueError, match="must be between"):
        mixin.create_api_access_token("huge", created_by=1, token_value=huge)

    assert _writes(db) == []


def test_generated_token_is_still_strong_and_accepted(monkeypatch):
    """Control: omitting token_value still mints a full-entropy credential."""
    mixin, db = _mixin(monkeypatch)
    token = mixin.create_api_access_token("generated", created_by=1)

    assert len(token.token) >= EXPECTED_MIN
    assert len(_writes(db)) == 1


def test_strong_custom_token_is_still_accepted(monkeypatch):
    """Control: a caller-supplied value at the generated bar is not blocked."""
    mixin, db = _mixin(monkeypatch)
    strong = "c" * EXPECTED_MIN

    token = mixin.create_api_access_token("custom", created_by=1, token_value=strong)

    assert token.token == strong
    assert len(_writes(db)) == 1
