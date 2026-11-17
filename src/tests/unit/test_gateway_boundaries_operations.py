"""Queued-operation, batch and snowflake-id boundaries."""

import pytest

from src.api.websocket.boundaries import (
    MAX_OP_BATCH,
    MAX_OP_PAYLOAD_BYTES,
    MAX_REPLAY_EVENT_BYTES,
    bounded_batch,
    parse_snowflake,
    payload_size,
)
from src.api.websocket.session import Session
from src.core.security.hardening import SNOWFLAKE_MAX, HardeningViolation


def test_payload_size_measures_serialized_bytes():
    assert payload_size({"a": 1}) < MAX_OP_PAYLOAD_BYTES
    assert payload_size({"d": "x" * (MAX_OP_PAYLOAD_BYTES + 1)}) > MAX_OP_PAYLOAD_BYTES


def test_bounded_batch_range():
    assert bounded_batch(10) == 10
    assert bounded_batch(None) == MAX_OP_BATCH
    with pytest.raises(HardeningViolation):
        bounded_batch(0)
    with pytest.raises(HardeningViolation):
        bounded_batch(MAX_OP_BATCH + 1)


@pytest.mark.parametrize("value", [0, -1, SNOWFLAKE_MAX + 1, "not-an-id", None])
def test_parse_snowflake_rejects_out_of_range(value):
    with pytest.raises(HardeningViolation):
        parse_snowflake(value, "artifact_id")


@pytest.mark.parametrize("value", [1, 42, str(SNOWFLAKE_MAX)])
def test_parse_snowflake_accepts_valid(value):
    assert parse_snowflake(value, "artifact_id") == int(value)


def test_session_replay_buffer_drops_oversized_events():
    session = Session(session_id="s", user_id=1)
    session.add_replay_event({"s": 1, "d": "ok"})
    session.add_replay_event({"s": 2, "d": "x" * (MAX_REPLAY_EVENT_BYTES + 1)})
    assert [event["s"] for event in session.replay_events] == [1]


def test_session_replay_capacity_is_bounded():
    session = Session(session_id="s", user_id=1, max_replay_events=10**9)
    assert session.max_replay_events == 100
    session = Session(session_id="s", user_id=1, max_replay_events=0)
    assert session.max_replay_events == 100
