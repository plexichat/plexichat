"""Regression tests for inbound federation id boundaries.

A signed PlexiJoin envelope proves *who* sent a payload, not that its ids are
safe to use. Two boundary gaps were left open after the strict validators were
introduced:

* ``artifact_op_inbound`` passed ``artifact_id``/``actor_id`` through bare
  ``int()``. That accepts zero, negatives and Python-only spellings such as
  ``"1_2"`` (parsed as 12), so the peer could name one id while a different
  one was looked up and broadcast to local artifact subscribers.
* ``submit_feedback`` reflected the raw exception string in its 500 response,
  disclosing backend details (CWE-209) to any authenticated caller.
"""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes import federation as mod
from src.core.auth.models import TokenInfo


def _request(payload: dict) -> Request:
    return cast(
        Request,
        SimpleNamespace(
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=json.dumps(payload).encode()),
        ),
    )


def _op_payload(**overrides):
    payload = {
        "artifact_id": 123,
        "op": {"op_type": "edit"},
        "actor_id": 7,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        0,  # sentinel id
        -1,  # negative id
        "0",
        "-5",
        "1_2",  # Python int() parses this as 12 (parser differential)
        "abc",
        "9223372036854775808",  # above the snowflake range, as text
    ],
)
async def test_artifact_op_rejects_non_snowflake_ids(bad_id):
    """Malformed ids must be rejected at the boundary, before any relay."""
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
        patch.object(mod, "_relay_op", new_callable=AsyncMock) as relay,
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.artifact_op_inbound(_request(_op_payload(artifact_id=bad_id)))

    assert exc.value.status_code == 400
    relay.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_actor", [-1, "-2", "1_2", "abc", "9223372036854775808"])
async def test_artifact_op_rejects_non_snowflake_actor_ids(bad_actor):
    """The actor id is spoofable in the relayed event if left unchecked."""
    payload = _op_payload(actor_id=bad_actor)

    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
        patch.object(mod, "_relay_op", new_callable=AsyncMock) as relay,
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.artifact_op_inbound(_request(payload))

    assert exc.value.status_code == 400
    relay.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact_id,actor_id,expected",
    [
        (1, 0, (1, 0)),  # actor 0 is the legit "no actor" sentinel
        ("990000000000000000", "7", (990000000000000000, 7)),
        (2**63 - 1, 2**63 - 1, (2**63 - 1, 2**63 - 1)),
    ],
)
async def test_artifact_op_accepts_valid_ids_as_ints(artifact_id, actor_id, expected):
    """Valid snowflakes reach the relay as canonical ints."""
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
        patch.object(mod, "_relay_op", new_callable=AsyncMock, return_value=2) as relay,
    ):
        result = await mod.artifact_op_inbound(
            _request(_op_payload(artifact_id=artifact_id, actor_id=actor_id))
        )

    assert result == {"ok": True, "delivered": 2}
    relay.assert_awaited_once_with(expected[0], {"op_type": "edit"}, expected[1])


@pytest.mark.asyncio
async def test_feedback_500_does_not_leak_internal_exception(monkeypatch):
    """A backend error string must never reach an authenticated client."""
    from src.api.routes import feedback as feedback_routes
    import src.core.feedback as core_feedback

    secret = "postgres://svc:s3cr3t@db.internal/app"

    def fake_submit(**kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(feedback_routes.config, "get", lambda *a, **k: True)
    monkeypatch.setattr(
        feedback_routes.ratelimit,
        "acheck_rate_limit",
        AsyncMock(return_value=SimpleNamespace(allowed=True)),
    )
    monkeypatch.setattr(core_feedback, "is_setup", lambda: True)
    monkeypatch.setattr(core_feedback, "submit_feedback", fake_submit)

    from src.api.schemas.feedback import FeedbackCreate

    user = cast(TokenInfo, SimpleNamespace(user_id=1))
    request = cast(
        Request,
        SimpleNamespace(state=SimpleNamespace(is_internal=False)),
    )

    with pytest.raises(HTTPException) as exc:
        await feedback_routes.submit_feedback(
            FeedbackCreate(content="A perfectly valid piece of feedback"),
            request,
            user,
        )

    assert exc.value.status_code == 500
    assert secret not in str(exc.value.detail)
