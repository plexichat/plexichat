"""Regression tests for the inbound join-request instance-id boundary.

The manager caps ``remote_instance_id`` length and rejects control characters,
but an instance id is an identifier — not free text. A signed payload can still
carry spaces, punctuation or Unicode look-alikes that pass the generic text
bound and end up persisted, shown to an admin for approval, and used in an
exact-match connection lookup. The route must apply the strict identifier
grammar before the value reaches the manager.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import federation as mod


class _InboundRequest:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.headers = {"content-type": "application/json"}
        self._payload = payload

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _invoke(payload: Dict[str, Any]) -> Tuple[Optional[dict], MagicMock, Optional[HTTPException]]:
    manager = MagicMock()
    manager.submit_inbound_request.return_value = {"id": 1}
    request = _InboundRequest(payload)
    error: Optional[HTTPException] = None
    result: Optional[dict] = None
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=manager),
        patch.object(mod, "_authenticate", return_value={"remote_url": ""}),
    ):
        try:
            result = asyncio.run(
                mod.join_request_inbound(request)  # type: ignore[arg-type]
            )
        except HTTPException as exc:
            error = exc
    return result, manager, error


@pytest.mark.parametrize(
    "instance_id",
    [
        "remote instance",  # whitespace: accepted by bound_text, not an identifier
        "remote.example/instance",  # slash is not in the identifier grammar
        "remøte.example",  # Unicode look-alike for an ASCII id
        "remote\ninstance",  # control character
        "x" * 129,  # over the identifier length cap
    ],
)
def test_malformed_instance_id_is_rejected_before_persisting(instance_id: str) -> None:
    _, manager, error = _invoke({"instance_id": instance_id})
    assert error is not None
    assert error.status_code == 400
    manager.submit_inbound_request.assert_not_called()


def test_canonical_instance_id_is_still_accepted() -> None:
    result, manager, error = _invoke({"instance_id": "remote.example:1"})
    assert error is None
    assert result is not None and result["ok"] is True
    manager.submit_inbound_request.assert_called_once()
    assert manager.submit_inbound_request.call_args.kwargs["remote_instance_id"] == (
        "remote.example:1"
    )
