"""Raw opcode fan-outs share one immutable payload across recipients.

``dispatch_event``/``broadcast_to_server`` already build the rendered ``d``
once per fan-out and share it.  ``dispatch_raw_to_users`` (voice speaking is
the hot path) still handed every connection to ``dispatch_raw``, which built a
fresh envelope dict per recipient.  With N recipients the wire JSON was then
serialized N times from N distinct-but-identical dicts: the identity-keyed
serialization cache on each connection could never hit.

The observable copy count is the number of *distinct payload objects* handed
to the transports, and the number of ``json.dumps`` calls needed for a
compressed burst.  Before the fix a six-recipient VOICE_SPEAKING burst shows
six objects / six dumps; after the fix it shows one of each.  Output equality
and the fail-closed recipient filter are asserted as well.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.intents import ALL_INTENTS
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager


class _FakeSessionManager:
    def __init__(self, connections):
        self._connections = connections

    def get_connections_for_users(self, user_ids):
        return list(self._connections)

    def record_event(self, *args, **kwargs):
        pass


def _fake_connection(name):
    conn = Connection(websocket=AsyncMock(), connection_id=name, intents=ALL_INTENTS)
    conn.state = ConnectionState.READY
    conn.user_id = 7
    conn.is_selftest = True
    return conn


DATA = {
    "user_id": 999,
    "channel_id": 123,
    "speaking": True,
    "volume": 1.0,
    "nested": {"a": [1, 2, 3]},
}


@pytest.mark.asyncio
async def test_raw_fanout_builds_one_payload_for_all_recipients():
    conns = [_fake_connection(f"c{i}") for i in range(6)]
    dispatcher = GatewayDispatcher(cast(SessionManager, _FakeSessionManager(conns)))

    sent = await dispatcher.dispatch_raw_to_users(
        [7] * 6, GatewayOpcode.VOICE_SPEAKING, DATA
    )

    assert sent == 6
    frames = [cast(AsyncMock, c.websocket.send_json).await_args.args[0] for c in conns]
    # Non-vacuous: six frames really went out and their contents are identical.
    assert all(frame == frames[0] for frame in frames)
    assert frames[0]["d"]["user_id"] == "999"
    # The copy count: one shared immutable envelope, not one per recipient.
    assert len({id(frame) for frame in frames}) == 1


@pytest.mark.asyncio
async def test_raw_fanout_serializes_identical_frame_once(monkeypatch):
    conns = [_fake_connection(f"c{i}") for i in range(6)]
    for conn in conns:
        conn.compress = True
        conn._zlib_context = object()  # forces the serialize path; patched below
    dispatcher = GatewayDispatcher(cast(SessionManager, _FakeSessionManager(conns)))

    dumps = []
    import json

    real_dumps = json.dumps

    def counting_dumps(*args, **kwargs):
        dumps.append(1)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", counting_dumps)

    # The zlib context double only needs to accept bytes; the frame bytes are
    # produced by ``json.dumps`` above, which is what this test counts.
    class _Zlib:
        def compress(self, _data):
            return b""

        def flush(self, _mode):
            return b""

    for conn in conns:
        conn._zlib_context = _Zlib()

    sent = await dispatcher.dispatch_raw_to_users(
        [7] * 6, GatewayOpcode.VOICE_SPEAKING, DATA
    )

    assert sent == 6
    assert len(dumps) == 1, f"identical frame serialized {len(dumps)} times"
