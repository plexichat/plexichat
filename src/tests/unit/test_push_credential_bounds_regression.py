"""Regression: push registration must bound every caller-supplied credential.

The length limits and ``_bounded_credential`` existed in ``tokens.py`` but were
never applied in ``register_token``. An authenticated caller could submit a
multi-megabyte "token"/subscription blob and force unbounded encryption, blind
indexing, memory use and row growth before the per-user token cap mattered.
These tests exercise the real registration entry point (not just the helper)
to pin that the bounds are actually enforced.
"""

from __future__ import annotations

import pytest

from src.core.push.manager import PushManager
from src.core.push.manager.tokens import (
    MAX_APP_VERSION_LENGTH,
    MAX_AUTH_LENGTH,
    MAX_DEVICE_ID_LENGTH,
    MAX_P256DH_LENGTH,
    MAX_TOKEN_LENGTH,
)


class _StubDB:
    """Minimal DB that satisfies PushManager construction; no real writes."""

    def execute(self, *args, **kwargs):
        return None

    def fetch_all(self, *args, **kwargs):
        return []

    def fetch_one(self, *args, **kwargs):
        return None

    def column_exists(self, *args, **kwargs):
        return True


@pytest.fixture
def manager() -> PushManager:
    return PushManager(_StubDB(), notifications_module=None)


def test_register_token_rejects_oversized_token(manager: PushManager):
    with pytest.raises(ValueError, match="maximum length"):
        manager.register_token(
            user_id=1,
            token="x" * (MAX_TOKEN_LENGTH + 1),
            platform="android",
        )


def test_register_token_requires_nonempty_token(manager: PushManager):
    with pytest.raises(ValueError, match="required"):
        manager.register_token(user_id=1, token="", platform="android")


@pytest.mark.parametrize(
    "field,limit",
    [
        ("p256dh", MAX_P256DH_LENGTH),
        ("auth", MAX_AUTH_LENGTH),
        ("device_id", MAX_DEVICE_ID_LENGTH),
        ("app_version", MAX_APP_VERSION_LENGTH),
    ],
)
def test_register_token_bounds_optional_credentials(
    manager: PushManager, field: str, limit: int
):
    kwargs = {"user_id": 1, "token": "valid-token", "platform": "web"}
    kwargs[field] = "y" * (limit + 1)
    with pytest.raises(ValueError, match="maximum length"):
        manager.register_token(**kwargs)


def test_register_token_accepts_bounded_credential(manager: PushManager):
    # Sanity check that the fix does not reject legitimate-length values; the
    # stub DB returns no inserted row so an empty sanitized record is fine.
    assert (
        manager.register_token(
            user_id=1,
            token="t" * MAX_TOKEN_LENGTH,
            platform="web",
            p256dh="k" * MAX_P256DH_LENGTH,
            auth="a" * MAX_AUTH_LENGTH,
        )
        == {}
    )
