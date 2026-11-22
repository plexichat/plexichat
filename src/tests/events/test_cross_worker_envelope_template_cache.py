"""Regression tests for identity-keyed cross-worker envelope serialization.

A burst of events with the same payload/audience (typing, presence, reactions)
used to re-run the whole ``json.dumps`` envelope on every publication even
though only the unique ``id`` differed. The dispatcher now memoizes the
serialized envelope by its complete identity and stamps the id into the cached
template. These tests pin the performance property, the correctness of the
stamped wire payload, and the cache's isolation/bounds.
"""

import json

import pytest

import src.core.database as db
import src.core.events.manager as manager_mod
from src.core.events.manager import (
    EventManager,
    clear_envelope_template_cache,
    get_envelope_template_cache_stats,
)
from src.core.events.models import Event
from src.core.events.types import EventType


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, channel, payload):
        self.published.append((channel, payload))


@pytest.fixture(autouse=True)
def client(monkeypatch):
    """Isolated fake Valkey client shared by every test in this module.

    Named ``client`` because each test requests it to inspect published
    envelopes; ``autouse=True`` keeps the cache-clearing teardown on every test.
    """
    clear_envelope_template_cache()
    manager_mod.cross_worker_dedupe.clear()
    client = _FakeClient()
    monkeypatch.setattr(db, "valkey_available", lambda: True)
    monkeypatch.setattr(db, "get_valkey_client", lambda: client)
    yield client
    clear_envelope_template_cache()
    manager_mod.cross_worker_dedupe.clear()


def _event(
    data=None,
    *,
    server_id=10,
    channel_id=None,
    event_type=EventType.TYPING_START,
):
    return Event(
        event_type=event_type,
        data=dict(data or {"user_id": 5}),
        server_id=server_id,
        channel_id=channel_id,
    )


def _manager(worker="worker-a"):
    em = EventManager()
    em.set_worker_id(worker)
    return em


def test_identical_envelope_is_serialized_once(client, monkeypatch):
    calls = []
    real_dumps = manager_mod.json.dumps

    def counting(*args, **kwargs):
        calls.append(1)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(manager_mod.json, "dumps", counting)

    em = _manager()
    for _ in range(100):
        em._publish_cross_worker(_event(), [1, 2, 3])

    # 1 full envelope template + 100 tiny json.dumps(event_id) stamps.
    assert len(calls) == 101
    stats = get_envelope_template_cache_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 99
    assert stats["size"] == 1
    assert len(client.published) == 100


def test_stamped_payload_matches_uncached_shape(client):
    em = _manager()
    event = _event({"user_id": 5, "nested": {"ids": [7, 8]}})
    em._publish_cross_worker(event, [1, 2, 3])

    parsed = json.loads(client.published[-1][1])
    assert parsed["t"] == event.event_type.value
    assert parsed["d"] == event.data
    assert parsed["u"] == [1, 2, 3]
    assert parsed["sw"] == "worker-a"
    assert parsed["scope"] == [10, None]
    assert parsed["id"].startswith("worker-a:")

    # A repeat reuses the template but still mints a fresh id; every other
    # field is byte-identical to the first publication.
    em._publish_cross_worker(
        _event({"user_id": 5, "nested": {"ids": [7, 8]}}), [1, 2, 3]
    )
    parsed2 = json.loads(client.published[-1][1])
    assert parsed2["id"] != parsed["id"]
    assert {k: v for k, v in parsed2.items() if k != "id"} == {
        k: v for k, v in parsed.items() if k != "id"
    }


def test_distinct_identities_do_not_share_templates(client):
    em = _manager()
    em._publish_cross_worker(_event({"user_id": 5}), [1, 2])
    em._publish_cross_worker(_event({"user_id": 6}), [1, 2])  # data
    em._publish_cross_worker(_event({"user_id": 5}), [1, 3])  # recipients
    em._publish_cross_worker(_event({"user_id": 5}, server_id=99), [1, 2])  # scope
    em._publish_cross_worker(
        _event({"user_id": 5}, event_type=EventType.MESSAGE_CREATE), [1, 2]
    )  # type

    stats = get_envelope_template_cache_stats()
    assert stats["misses"] == 5
    assert stats["size"] == 5
    assert {tuple(json.loads(p)["u"]) for _, p in client.published} == {
        (1, 2),
        (1, 3),
    }


def test_marker_in_payload_does_not_corrupt_the_envelope_id(client):
    marker = manager_mod._ENVELOPE_ID_MARKER
    em = _manager()
    em._publish_cross_worker(_event({"note": marker}), [1])

    parsed = json.loads(client.published[-1][1])
    assert parsed["d"]["note"] == marker
    assert parsed["id"].startswith("worker-a:")
    assert parsed["id"] != marker


def test_template_cache_is_bounded(client):
    em = _manager()
    for i in range(200):
        em._publish_cross_worker(_event({"i": i}), [1])

    stats = get_envelope_template_cache_stats()
    assert stats["size"] == manager_mod._MAX_ENVELOPE_TEMPLATES
    assert stats["misses"] == 200
