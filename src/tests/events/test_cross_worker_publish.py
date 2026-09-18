"""Tests for the cross-worker publish classification.

``EventManager._publish_cross_worker`` must distinguish three backend
states: Valkey unavailable (silent local-only no-op, no drop counted),
publish error mid-operation (drop counted + debug, surfaced via
``get_loss_metrics``), and unserializable payload (deterministic caller
bug — warn loudly instead of blaming the backend).
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

import src.core.database as database_module
from src.core.events.models import Event
from src.core.events.types import EventType

pytestmark = pytest.mark.unit


def _make_em(events_module):
    return events_module._manager


def _make_event(data=None):
    return Event(
        event_type=EventType.PRESENCE_UPDATE, data={"k": "v"} if data is None else data
    )


def _valkey_mocks(monkeypatch, *, available=True, publish=None):
    client = MagicMock()
    if publish is not None:
        client.publish.side_effect = publish
    monkeypatch.setattr(database_module, "valkey_available", lambda: available)
    monkeypatch.setattr(database_module, "get_valkey_client", lambda: client)
    return client


def test_unavailable_is_silent_local_noop(events_module, monkeypatch):
    em = _make_em(events_module)
    client = _valkey_mocks(monkeypatch, available=False)

    em._publish_cross_worker(_make_event(), [101])

    client.publish.assert_not_called()
    assert em._cross_worker_drops == 0
    assert em.get_loss_metrics()["cross_worker_drops"] == 0


def test_publish_error_counts_drop(events_module, monkeypatch):
    em = _make_em(events_module)
    client = _valkey_mocks(
        monkeypatch, available=True, publish=RuntimeError("connection lost")
    )

    em._publish_cross_worker(_make_event(), [101])
    em._publish_cross_worker(_make_event(), [102])

    assert client.publish.call_count == 2
    assert em._cross_worker_drops == 2
    assert em.get_loss_metrics()["cross_worker_drops"] == 2


def test_success_publishes_tagged_payload(events_module, monkeypatch):
    em = _make_em(events_module)
    em.set_worker_id("worker-1")
    client = _valkey_mocks(monkeypatch, available=True)

    em._publish_cross_worker(_make_event(), [101, 102])

    assert client.publish.call_count == 1
    channel, payload = client.publish.call_args[0]
    body = json.loads(payload)
    assert body["t"] == EventType.PRESENCE_UPDATE.value
    assert body["d"] == {"k": "v"}
    assert body["u"] == [101, 102]
    assert body["sw"] == "worker-1"
    assert em._cross_worker_drops == 0


def test_unserializable_payload_warns_without_drop(events_module, monkeypatch, caplog):
    em = _make_em(events_module)
    client = _valkey_mocks(monkeypatch, available=True)

    with caplog.at_level("WARNING", logger="src.core.events.manager"):
        em._publish_cross_worker(_make_event(data={"bad": object()}), [101])

    client.publish.assert_not_called()
    assert em._cross_worker_drops == 0


def test_import_failure_is_silent_local_noop(events_module, monkeypatch):
    em = _make_em(events_module)
    monkeypatch.setitem(sys.modules, "src.core.database", None)

    # Must not raise: single-instance mode keeps the event local.
    em._publish_cross_worker(_make_event(), [101])

    assert em._cross_worker_drops == 0


def test_probe_failure_is_silent_local_noop(events_module, monkeypatch):
    em = _make_em(events_module)

    def _boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(database_module, "valkey_available", _boom)
    monkeypatch.setattr(database_module, "get_valkey_client", lambda: MagicMock())

    em._publish_cross_worker(_make_event(), [101])

    assert em._cross_worker_drops == 0


def test_loss_metrics_include_cross_worker_drops(events_module):
    em = _make_em(events_module)
    metrics = em.get_loss_metrics()
    assert metrics["cross_worker_drops"] == 0
