"""Regression: webhook enqueue serializes a fanned-out payload once per endpoint.

One event delivered to N webhook endpoints calls ``enqueue`` N times with the
same payload. It used to build the canonical JSON twice per call — once for
the stored row / size bound and again inside ``sign_payload`` — so a 50-way
fan-out paid for 100 full encodes of one unchanged payload. Enqueue now encodes
once and hands the bytes to the signer.

The tests below count canonical ``json.dumps`` calls (identified by the
``sort_keys`` + compact-separator kwargs) so the before/after difference is
observable without relying on wall-clock timing.
"""

import json

import pytest

import src.core.webhooks.canonical as canonical_module
import src.core.webhooks.retry as retry_module
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.webhooks.canonical import canonical_payload_bytes
from src.core.webhooks.retry import WebhookRetryQueue


ENDPOINT = "https://example.com/hook"
PAYLOAD = {
    "event": "message.created",
    "id": 123456789012345678,
    "author": {"id": 987654321098765432, "name": "user"},
    "content": "hello " * 50,
    "embeds": [{"title": "t" * 80} for _ in range(5)],
}


class _FakeCursor:
    rowcount = 1


class _FakeDB:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=None):
        self.rows.append((sql, params))
        return _FakeCursor()


class _NoLog:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _BytesManager:
    """Current manager: exposes sign_payload_bytes and never re-encodes."""

    def __init__(self):
        self.calls = []

    def sign_payload(self, webhook_id, payload):
        raise AssertionError("sign_payload must not be used when bytes are available")

    def sign_payload_bytes(self, webhook_id, payload_bytes):
        self.calls.append((webhook_id, payload_bytes))
        return {
            "X-Plexichat-Signature": "sig",
            "X-Plexichat-Timestamp": "1",
        }


class _LegacyManager:
    """Old/injected manager: only sign_payload, which encodes again."""

    def sign_payload(self, webhook_id, payload):
        canonical_payload_bytes(payload)
        return {
            "X-Plexichat-Signature": "legacy",
            "X-Plexichat-Timestamp": "1",
        }


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(
        retry_module,
        "validate_delivery_endpoint",
        lambda url, allow_private_network=False: ("example.com", "93.184.216.34"),
    )
    monkeypatch.setattr(retry_module, "logger", _NoLog())


def _queue(manager):
    q = WebhookRetryQueue(_FakeDB(), webhook_manager=manager)
    q._generate_id = lambda: 1
    q._get_timestamp = lambda: 1_700_000_000_000
    return q


@pytest.fixture
def count_canonical_dumps(monkeypatch):
    """Count json.dumps calls that build canonical webhook payload bytes."""
    real_dumps = json.dumps
    calls = []

    def counting_dumps(*args, **kwargs):
        if kwargs.get("sort_keys") is True and kwargs.get("separators") == (",", ":"):
            calls.append(args[0] if args else None)
        return real_dumps(*args, **kwargs)

    # canonical.py and retry.py share the stdlib json module object.
    monkeypatch.setattr(canonical_module.json, "dumps", counting_dumps)
    return calls


def test_single_enqueue_serializes_once(count_canonical_dumps):
    _queue(_BytesManager()).enqueue(
        webhook_id=42,
        event_type="message.created",
        payload=PAYLOAD,
        endpoint_url=ENDPOINT,
    )
    assert len(count_canonical_dumps) == 1
    assert count_canonical_dumps[0]["event"] == "message.created"


def test_fanout_serializes_once_per_endpoint(count_canonical_dumps):
    manager = _BytesManager()
    q = _queue(manager)
    for i in range(50):
        q.enqueue(
            webhook_id=1000 + i,
            event_type="message.created",
            payload=PAYLOAD,
            endpoint_url=ENDPOINT,
        )
    # 50 canonical encodes instead of the old 100 (50 stored + 50 signed).
    assert len(count_canonical_dumps) == 50
    assert len(manager.calls) == 50


def test_legacy_manager_still_signs_with_two_encodes(count_canonical_dumps):
    """Back-compat path; also documents the pre-optimization call count."""
    q = _queue(_LegacyManager())
    q.enqueue(
        webhook_id=42,
        event_type="message.created",
        payload=PAYLOAD,
        endpoint_url=ENDPOINT,
    )
    assert len(count_canonical_dumps) == 2


def test_reused_bytes_are_exactly_the_signed_bytes():
    manager = _BytesManager()
    q = _queue(manager)
    record = q.enqueue(
        webhook_id=42,
        event_type="message.created",
        payload=PAYLOAD,
        endpoint_url=ENDPOINT,
    )
    stored_json = q._db.rows[0][1][3]
    expected_bytes = canonical_payload_bytes(stringify_snowflake_ids(PAYLOAD))
    assert manager.calls[0][1] == expected_bytes
    assert stored_json == expected_bytes.decode("utf-8")
    assert json.loads(stored_json)["id"] == str(PAYLOAD["id"])
    assert record["status"] == "pending"


def test_size_bound_still_enforced():
    q = _queue(_BytesManager())
    big = {"blob": "x" * (WebhookRetryQueue.MAX_PAYLOAD_SIZE + 1)}
    with pytest.raises(ValueError, match="exceeds maximum size"):
        q.enqueue(
            webhook_id=42,
            event_type="message.created",
            payload=big,
            endpoint_url=ENDPOINT,
        )
    assert q._db.rows == []
