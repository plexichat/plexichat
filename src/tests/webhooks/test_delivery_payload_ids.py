"""Ratchet: outbound webhook payloads must carry snowflake ids as strings.

An outbound webhook receiver is an external HTTP consumer — almost always
JavaScript. A JS ``Number`` cannot hold a 64-bit snowflake exactly, so a
numeric ``id``/``*_id`` crossing the wire is silently rounded and the
receiver addresses the wrong entity.

``WebhookRetryQueue.enqueue`` is the single store every delivery flows
through; it must normalize ids BEFORE signing, because the signature covers
the exact JSON body that is POSTed. These tests pin both properties:

* ids are stringified wherever they appear (nested, and ``*_ids`` lists);
* non-id numbers (timestamps, counts, flags) are left numeric;
* the payload handed to the signer is byte-for-byte the payload delivered.
"""

from typing import Any, Dict

import pytest

from src.core.events.snowflakes import is_id_field
from src.core.webhooks.delivery import WebhookDeliverer, process_due_retries
from src.core.webhooks.retry import WebhookRetryQueue

# 2**53 + 1: the smallest integer a double cannot represent exactly.
LOSSY = 2**53 + 1


class _RecorderClient:
    """Fake HTTP client that records every POST body."""

    def __init__(self):
        self.calls: list[Dict[str, Any]] = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(200)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _RecordingSigner:
    """Stand-in for ``WebhookManager`` recording what it signed."""

    def __init__(self):
        self.signed: list[Dict[str, Any]] = []

    def sign_payload(self, webhook_id, payload):
        self.signed.append(payload)
        return {"X-Plexichat-Signature": "sig", "X-Plexichat-Timestamp": "1"}


@pytest.fixture
def retry_queue(db):
    return WebhookRetryQueue(db)


def _numeric_payload() -> Dict[str, Any]:
    return {
        "id": LOSSY,
        "event": "MESSAGE_CREATE",
        "content": "hello",
        "timestamp": 1760000000000,
        "flags": 3,
        "author": {"id": LOSSY + 2, "username": "ada", "bot": False},
        "mentions": [{"id": LOSSY + 4}, {"id": LOSSY + 6}],
        "recipient_ids": [LOSSY + 8, LOSSY + 10],
        "channel": {"id": LOSSY + 12, "type": 0},
    }


def _walk_id_values(node, key=""):
    """Yield ``(path, value)`` for every id-typed leaf in a payload."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _walk_id_values(value, name)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_id_values(value, key)
    elif is_id_field(key):
        yield key, node


def test_enqueue_never_persists_a_numeric_snowflake(retry_queue):
    """The stored payload must stringify every id and keep other numbers."""
    record = retry_queue.enqueue(
        webhook_id=1,
        event_type="message.created",
        payload=_numeric_payload(),
        endpoint_url="https://example.com/hook",
    )

    row = retry_queue._db.fetch_one(
        "SELECT payload FROM webhook_retry_queue WHERE id = ?", (record["id"],)
    )
    import json

    stored = json.loads(row["payload"])

    for key, value in _walk_id_values(stored):
        assert isinstance(value, str), f"id {key!r} persisted numeric: {value!r}"
    # Non-id scalars are untouched.
    assert stored["timestamp"] == 1760000000000
    assert stored["flags"] == 3
    assert stored["author"]["bot"] is False


def test_delivered_body_stringifies_ids(retry_queue):
    """What actually reaches the endpoint must carry string ids."""
    retry_queue.enqueue(
        webhook_id=1,
        event_type="message.created",
        payload=_numeric_payload(),
        endpoint_url="https://example.com/hook",
    )
    client = _RecorderClient()
    process_due_retries(retry_queue, WebhookDeliverer(client=client))

    assert len(client.calls) == 1
    body = client.calls[0]["json"]
    for key, value in _walk_id_values(body):
        assert isinstance(value, str), f"id {key!r} delivered numeric: {value!r}"


def test_signer_sees_the_delivered_body(retry_queue):
    """Signature and body must agree, or every receiver rejects the call.

    The signature is computed over the payload the receiver validates; if
    ids were stringified after signing, the receiver's HMAC would not match.
    """
    signer = _RecordingSigner()
    queue = WebhookRetryQueue(retry_queue._db, webhook_manager=signer)
    queue.enqueue(
        webhook_id=1,
        event_type="message.created",
        payload=_numeric_payload(),
        endpoint_url="https://example.com/hook",
    )
    client = _RecorderClient()
    process_due_retries(queue, WebhookDeliverer(client=client))

    assert len(signer.signed) == 1
    assert signer.signed[0] == client.calls[0]["json"]
    for key, value in _walk_id_values(signer.signed[0]):
        assert isinstance(value, str), f"signed id {key!r} was numeric: {value!r}"
