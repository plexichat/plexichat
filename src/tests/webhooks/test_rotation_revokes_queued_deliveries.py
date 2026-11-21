"""Regression: signing-key rotation must invalidate already-queued deliveries.

The background delivery worker drains ``webhook_retry_queue`` rows using the
signing headers captured at enqueue time. It re-signs only when those headers
are *stale* (>5 min old); a freshly captured signature is reused verbatim.
``rotate_signing_key`` promises no grace window for the retired key, but it
never touched the queue: a delivery enqueued seconds before a rotation was
still POSTed with a signature made by the retired key. A receiver still
trusting the old public key accepts that delivery as authentic, so the
retired key stays live for queued work. This test drives the real manager,
retry queue and deliverer and fails while a queued row keeps the old key.
"""

import base64
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.core.webhooks.canonical import canonical_payload_bytes
from src.core.webhooks.delivery import WebhookDeliverer
from src.core.webhooks.manager.constants import (
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
    SIGNATURE_VERSION,
)
from src.core.webhooks.retry import WebhookRetryQueue

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _CapturingClient:
    """Minimal HTTP client that records the headers of the single POST."""

    follow_redirects = False

    def __init__(self):
        self.headers = None

    def post(self, url, json=None, headers=None, **kwargs):
        self.headers = dict(headers or {})

        class _Response:
            status_code = 200
            headers = {}

        return _Response()


def _public_key(db, webhook_id):
    row = db.fetch_one(
        "SELECT signing_key_public FROM webhook_webhooks WHERE id = ?", (webhook_id,)
    )
    value = row["signing_key_public"]
    return value if isinstance(value, bytes) else base64.b64decode(value)


def _signature_verifies(public_key_bytes, headers, payload):
    """Return True iff headers' signature verifies under ``public_key_bytes``."""
    signed_data = f"{SIGNATURE_VERSION}.{headers[SIGNATURE_TIMESTAMP_HEADER]}.".encode(
        "utf-8"
    ) + canonical_payload_bytes(payload)
    signature = base64.b64decode(headers[SIGNATURE_HEADER])
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature, signed_data
        )
        return True
    except InvalidSignature:
        return False


def test_rotation_invalidates_queued_signatures(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]
    manager = setup["webhooks"]
    webhook_id = setup["webhook"].id

    # A delivery is genuinely queued (and signed) with the CURRENT key.
    queue = WebhookRetryQueue(db, webhook_manager=manager)
    payload = {"content": "signed before rotation"}
    queued = queue.enqueue(
        webhook_id=webhook_id,
        event_type="message.created",
        payload=payload,
        endpoint_url="https://example.com/hook",
    )
    row = db.fetch_one(
        "SELECT metadata FROM webhook_retry_queue WHERE id = ?", (queued["id"],)
    )
    original_headers = json.loads(row["metadata"])["original_signing_headers"]
    old_public = _public_key(db, webhook_id)
    assert _signature_verifies(old_public, original_headers, payload)

    # Admin rotates the key; the retired key must not remain a valid signer.
    manager.rotate_signing_key(webhook_id, setup["owner"].id)
    new_public = _public_key(db, webhook_id)
    assert new_public != old_public
    assert not _signature_verifies(new_public, original_headers, payload)

    # The still-queued delivery must now be re-signed with the NEW key, never
    # POSTed with a signature made by the retired one.
    pending = queue.get_pending_retries(limit=10)
    assert [r["id"] for r in pending] == [queued["id"]]
    client = _CapturingClient()
    deliverer = WebhookDeliverer(client=client, signer=manager.sign_payload)
    assert deliverer.deliver(pending[0]) == 200

    sent = client.headers
    assert _signature_verifies(new_public, sent, payload), (
        "queued delivery still carries a signature from the retired key"
    )
    assert not _signature_verifies(old_public, sent, payload)
