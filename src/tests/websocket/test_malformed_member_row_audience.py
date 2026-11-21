"""Gateway audience gates must not admit users via malformed member rows.

Two batched audience resolvers build an allow set from a broad
``srv_members`` / ``msg_participants`` projection and then coerce every value
with ``int()``:

* ``GatewayDispatcher._resolve_server_member_ids`` resolves server-broadcast
  recipients from ``get_member_user_ids``;
* ``GatewayDispatcher.broadcast_ratchet_update`` resolves the authoritative
  conversation participant set from ``get_participant_ids`` and intersects a
  caller-supplied connection snapshot with it.

In both, a malformed-but-coercible DB value (``"05"``, a padded string, a
float, or ``True``) silently becomes a real user id and enters the allow set,
even though the row does not prove that user's membership. That leaks server
events / private ratchet metadata to a user who is not a member. Only genuine
integer ids from live rows may enter these audiences.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.api.websocket as ws
from src.api.websocket.dispatcher import GatewayDispatcher

pytestmark = pytest.mark.unit

_SERVER = 99
_CONV = 42
_UPDATE = {"reason": "rotation", "new_interval_id": 999, "at": 1700000000000}


def _conn(user_id):
    conn = MagicMock()
    conn.is_authenticated = True
    conn.connection_id = f"c{user_id}"
    conn.session_id = None
    conn.user_id = user_id
    seq = [0]

    def _incr(_seq=seq):
        _seq[0] += 1
        return _seq[0]

    conn.increment_sequence.side_effect = _incr
    conn.send_json = AsyncMock(return_value=True)
    return conn


class _Servers:
    def get_member_user_ids(self, server_id):
        return ["05", 7, " 8 ", True, 5.9]


def test_malformed_member_rows_do_not_enter_server_audience(monkeypatch):
    """''/float/bool member ids must not become broadcast recipients."""
    monkeypatch.setattr(ws, "get_servers_module", lambda: _Servers())
    dispatcher = GatewayDispatcher.__new__(GatewayDispatcher)

    assert dispatcher._resolve_server_member_ids(_SERVER) == {7}


@pytest.mark.asyncio
async def test_malformed_participant_rows_do_not_enter_ratchet_audience():
    """A malformed participant id must not keep a non-member connection."""
    dispatcher = GatewayDispatcher(session_manager=MagicMock())
    # Caller-supplied snapshot names users 5, 7, 1 and 8.
    conns = [_conn(5), _conn(7), _conn(1), _conn(8)]

    # Authoritative DB participants: only the genuine int 7 is a real member.
    with patch(
        "src.core.messaging.get_participant_ids",
        return_value=["05", 7, True, 5.9],
    ):
        sent = await dispatcher.broadcast_ratchet_update(
            _CONV, _UPDATE, connections=conns
        )

    assert sent == 1, "only the genuine integer participant may receive the frame"
    assert conns[1].send_json.called  # user 7 (real int)
    assert not conns[0].send_json.called  # user 5 (from "05" / 5.9)
    assert not conns[2].send_json.called  # user 1 (from True)
    assert not conns[3].send_json.called  # user 8 never a participant
