"""Regression tests for the ``after`` cursor boundary on reaction-user reads.

``GET .../reactions/{emoji}`` uses ``after`` as a user-id pagination cursor,
which the manager turns into a ``user_id > ?`` predicate. The cursor used to be
parsed with bare ``int()`` on the raw query string, so homoglyph digits, PEP
515 underscores, negatives and values past the snowflake range were accepted
with a different meaning than the token the caller sent (and an out-of-SQLite-
range value surfaced as a 500 at bind time). These tests drive real requests
through the router and assert the cursor shares the ASCII snowflake gate used
by the message id in the same handler.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth.models import AccountType, TokenInfo


class _RecorderReactions:
    """Fake reactions manager recording the parsed cursor it was handed."""

    def __init__(self):
        self.calls = []

    def get_reaction_users(self, user_id, message_id, emoji, limit, after_user_id=None):
        self.calls.append(
            {
                "user_id": user_id,
                "message_id": message_id,
                "emoji": emoji,
                "limit": limit,
                "after_user_id": after_user_id,
            }
        )
        return []


def _token_info() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=7,
        user_id=7,
        session_id=1,
        permissions={},
        rate_limit_tier="user",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


@pytest.fixture
def cursor_env(monkeypatch):
    """Host ONLY the reactions router with auth and the manager stubbed."""
    from src.api.middleware.authentication import get_current_user
    from src.api.routes import reactions as reactions_routes

    recorder = _RecorderReactions()
    monkeypatch.setattr(reactions_routes.api, "get_reactions", lambda: recorder)

    app = FastAPI()
    app.include_router(reactions_routes.router)
    app.dependency_overrides[get_current_user] = _token_info
    return TestClient(app), recorder


def _get(client, after=None):
    params = {} if after is None else {"after": after}
    return client.get("/channels/42/messages/99/reactions/%F0%9F%91%8D", params=params)


def test_valid_cursor_reaches_manager_as_int(cursor_env):
    client, recorder = cursor_env
    resp = _get(client, after="123")
    assert resp.status_code == 200, resp.text
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["after_user_id"] == 123
    assert recorder.calls[0]["message_id"] == 99


def test_cursor_omitted_or_zero_means_no_filter(cursor_env):
    client, recorder = cursor_env
    assert _get(client).status_code == 200
    assert _get(client, after="0").status_code == 200
    # "0" is a valid non-negative snowflake and is passed through as-is; the
    # manager's ``if after_user_id:`` treats 0 as an unset cursor, exactly as
    # it did with the old bare-int parse. Preserve that behaviour.
    assert [c["after_user_id"] for c in recorder.calls] == [None, 0]


@pytest.mark.parametrize(
    "bad_cursor",
    [
        "-1",  # negative -> no upper/lower snowflake bound
        "1_2",  # PEP 515 underscore -> int() silently reads 12
        "٤٢",  # Arabic-Indic homoglyph digits
        "４２",  # fullwidth homoglyph digits
        "9223372036854775808",  # 2**63: past the 64-bit snowflake max
        "9" * 64,  # accepted by max_length=64, overflows SQLite INTEGER
    ],
)
def test_invalid_cursor_rejected_before_manager(cursor_env, bad_cursor):
    client, recorder = cursor_env
    resp = _get(client, after=bad_cursor)
    assert resp.status_code == 400, (bad_cursor, resp.text)
    assert recorder.calls == [], bad_cursor
