"""
Regression tests for finding L36 (artifacts): a full artifact payload can
reach ~1 MB, and the gateway used to ship it WHOLE to every late joiner, so
N concurrent joins pushed N x 1 MB frames. The sync path now caps the
payload: oversized payloads are omitted (flagged ``snapshot_truncated``) and
the client fetches the body over REST instead.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.api.websocket.artifacts import (
    _MAX_SNAPSHOT_PAYLOAD_BYTES,
    cap_snapshot_payload,
    send_artifact_sync,
)


def _connection():
    conn = MagicMock()
    conn.is_selftest = True
    conn.send_json = AsyncMock(return_value=True)
    return conn


def test_cap_snapshot_small_payload_passes_through():
    payload = {"content": "hello", "language": "python"}
    snapshot, truncated = cap_snapshot_payload(payload)
    assert truncated is False
    assert snapshot == payload


def test_cap_snapshot_oversized_payload_omitted():
    # Slightly above the cap: a single big string is enough.
    big = "x" * (_MAX_SNAPSHOT_PAYLOAD_BYTES + 1)
    payload = {"content": big}
    snapshot, truncated = cap_snapshot_payload(payload)
    assert truncated is True
    assert snapshot == {}


def test_cap_snapshot_non_dict_fails_closed():
    snapshot, truncated = cap_snapshot_payload("not-a-dict")
    assert truncated is True
    assert snapshot == {}


def test_send_sync_marks_truncated_frame():
    conn = _connection()
    sent = asyncio.run(
        send_artifact_sync(
            conn,
            123,
            {},
            ops=[],
            rate_limit_per_minute=1000,
            snapshot_truncated=True,
        )
    )
    assert sent is True
    payload = conn.send_json.call_args.args[0]
    assert payload["op"] == 63  # ARTIFACT_SYNC
    d = payload["d"]
    assert d["artifact_id"] == "123"
    assert d["snapshot"] == {}
    assert d["snapshot_truncated"] is True


def test_send_sync_defaults_not_truncated():
    conn = _connection()
    asyncio.run(
        send_artifact_sync(
            conn,
            123,
            {"content": "small"},
            ops=[{"seq": 1}],
            rate_limit_per_minute=1000,
        )
    )
    d = conn.send_json.call_args.args[0]["d"]
    assert d["snapshot"] == {"content": "small"}
    assert d["snapshot_truncated"] is False
    assert d["ops"] == [{"seq": 1}]
