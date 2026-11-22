"""Share-link password guessing must be bounded at the route boundary.

A Stash share link's token is a bearer credential. When a link is additionally
password-protected, the manager verifies the password on every resolve / ticket
request. Without a dedicated gate an attacker holding the token (e.g. from a
leaked chat message) can guess the password at the process-wide user/IP rate
(default: thousands of requests per user), making the password decorative.

The fix adds a per-link, token-fingerprint-keyed attempt budget that is checked
BEFORE the manager's password verification, so failed guesses count too. These
tests lock in both the fail-closed behavior and the no-token-leak property of
the rate-limit key.
"""

from __future__ import annotations

import asyncio
import hashlib
import types

import pytest
from fastapi import HTTPException

from src.api.routes.suite import stash as pkg
from src.api.routes.suite.stash import share_links

USER = types.SimpleNamespace(user_id=42)
TOKEN = "opaque-share-link-token-value"


class RecordingManager:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {}

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return self.result

    def resolve_share_link(self, *args, **kwargs):
        return self._record("resolve_share_link", *args, **kwargs)

    def create_share_download_ticket(self, *args, **kwargs):
        return self._record("create_share_download_ticket", *args, **kwargs)


@pytest.fixture
def manager(monkeypatch):
    rec = RecordingManager()
    monkeypatch.setattr(pkg, "_manager", lambda: rec)
    return rec


def _deny(monkeypatch):
    monkeypatch.setattr(share_links, "check_rate_limit", lambda *a, **k: (False, 0))


def _allow(monkeypatch):
    monkeypatch.setattr(share_links, "check_rate_limit", lambda *a, **k: (True, 9))


def test_rate_limit_key_is_a_token_fingerprint():
    key = share_links._share_link_verify_key(TOKEN)
    assert key.startswith("stash:share-link-verify:")
    assert TOKEN not in key
    assert key.endswith(hashlib.sha256(TOKEN.encode("utf-8")).hexdigest())
    # The same token always maps to the same bucket; distinct tokens do not.
    assert key == share_links._share_link_verify_key(TOKEN)
    assert key != share_links._share_link_verify_key(TOKEN + "x")


@pytest.mark.parametrize(
    "handler_name, body, result",
    [
        (
            "resolve_share_link",
            share_links.StashShareLinkResolveRequest(token=TOKEN, password="guess"),
            {"item_id": "123", "permissions": ["download"]},
        ),
        (
            "share_link_download",
            share_links.StashShareDownloadRequest(token=TOKEN, password="guess"),
            {"ticket": "t" * 32, "item_id": "123", "expires_at": 1},
        ),
    ],
)
def test_exhausted_budget_blocks_before_password_check(
    monkeypatch, manager, handler_name, body, result
):
    _deny(monkeypatch)
    manager.result = result
    handler = getattr(share_links, handler_name)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(body=body, current_user=USER))

    assert exc.value.status_code == 429
    assert manager.calls == [], "password verification must not run once exhausted"


@pytest.mark.parametrize(
    "handler_name, body, result, expected_call",
    [
        (
            "resolve_share_link",
            share_links.StashShareLinkResolveRequest(token=TOKEN, password="guess"),
            {"item_id": "123", "permissions": ["download"]},
            "resolve_share_link",
        ),
        (
            "share_link_download",
            share_links.StashShareDownloadRequest(token=TOKEN, password="guess"),
            {"ticket": "t" * 32, "item_id": "123", "expires_at": 1},
            "create_share_download_ticket",
        ),
    ],
)
def test_allowed_attempt_reaches_manager(
    monkeypatch, manager, handler_name, body, result, expected_call
):
    _allow(monkeypatch)
    manager.result = result
    handler = getattr(share_links, handler_name)

    response = asyncio.run(handler(body=body, current_user=USER))

    assert len(manager.calls) == 1
    assert manager.calls[0][0] == expected_call
    assert response is not None
