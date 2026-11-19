"""Regression tests for federation route validators accepting trailing newlines.

The route-level regexes used ``re.match`` with a ``$`` anchor. In Python ``$``
also matches immediately *before* a trailing newline, so a signed federation
payload could smuggle a control character past every route validator — and a
max-length value plus one newline slipped past the length bound encoded in the
regex quantifier. The PlexiJoin token validator already switched to
``fullmatch`` for this exact bug; these tests pin the route validators to the
same behaviour and cover the ``op_type`` relay gap that let an unvalidated op
token reach local subscribers.
"""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes import federation as mod


def _request(payload: dict) -> Request:
    return cast(
        Request,
        SimpleNamespace(
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=json.dumps(payload).encode()),
        ),
    )


class TestTrailingNewlineRejected:
    @pytest.mark.parametrize(
        "bad",
        [
            "id\nx",  # embedded control character (strip() does not remove it)
            "instance\rx",
            "a" * 129,  # over the maximum length
        ],
    )
    def test_remote_id_rejects_control_characters(self, bad):
        with pytest.raises(HTTPException) as exc:
            mod.validate_remote_id(bad, "instance_id")
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "bad",
        [
            "code123\n",
            "a" * 64 + "\n",  # 65 chars incl. newline slipped the {1,64} bound
            "code\r\n",
            "code\n123",
        ],
    )
    def test_invite_code_rejects_control_characters(self, bad):
        with pytest.raises(HTTPException) as exc:
            mod.validate_invite_code(bad, "invite_code")
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("bad", ["edit\n", "a" * 128 + "\n", "bad\ntype"])
    def test_type_field_rejects_control_characters(self, bad):
        with pytest.raises(HTTPException) as exc:
            mod.validate_type_field(bad, "message_type")
        assert exc.value.status_code == 400

    def test_valid_values_still_accepted(self):
        assert mod.validate_remote_id("instance-1", "instance_id") == "instance-1"
        assert mod.validate_invite_code("abc_123-XY", "invite_code") == "abc_123-XY"
        assert mod.validate_type_field("edit", "op_type") == "edit"
        assert mod.validate_type_field("guild.create", "op_type") == "guild.create"

    def test_nested_invite_code_with_newline_is_rejected(self):
        payload = {"meta": {"invite_code": "code123\n"}}
        with pytest.raises(HTTPException) as exc:
            mod.validate_invite_codes(payload)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_message_inbound_rejects_newline_invite_code():
    """A signed message with a newline in an invite_code is refused end to end."""
    payload = {
        "message_type": "join",
        "invite_code": "code123\n",
    }
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.message_inbound(_request(payload))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_op_type", ["edit\n", {"op": "edit"}, 123, "x" * 200])
async def test_artifact_op_rejects_unvalidated_op_type(bad_op_type):
    """op_type is bound to the token grammar before any local relay."""
    payload = {
        "artifact_id": 123,
        "op": {"op_type": bad_op_type},
        "actor_id": 7,
    }
    with (
        patch.object(mod, "_get_plexijoin_manager", return_value=MagicMock()),
        patch.object(mod, "_authenticate", return_value={"id": 1}),
        patch.object(mod, "_relay_op", new_callable=AsyncMock) as relay,
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.artifact_op_inbound(_request(payload))
    assert exc.value.status_code == 400
    relay.assert_not_called()
