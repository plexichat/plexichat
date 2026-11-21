"""Regression: signing-key rotation must survive an in-flight row being re-claimed.

A delivery already claimed as ``delivering`` cannot be recalled from a live
worker, but its queue row is not gone: when that worker dies,
``WebhookRetryQueue.cleanup_expired`` re-claims the stuck row back to
``pending`` so another worker can deliver it. The delivery worker reuses the
signing headers captured at enqueue time verbatim while their timestamp is
inside the replay window, so if rotation left those headers on a ``delivering``
row, the later re-claimed attempt would still be POSTed with a signature made
by the RETIRED key -- an unbounded grace window for a key that was supposed to
be discarded. This test drives the real manager, retry queue, cleanup path and
deliverer and fails while a re-claimed row keeps the old key's signature.
"""

import base64
import json
import time

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
    if not headers:
        return False
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


def test_rotation_revokes_signature_on_reclaimed_inflight_delivery(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]
    manager = setup["webhooks"]
    webhook_id = setup["webhook"].id

    # A delivery is genuinely queued and signed with the CURRENT key.
    queue = WebhookRetryQueue(db, webhook_manager=manager)
    payload = {"content": "signed while in flight before rotation"}
    queued = queue.enqueue(
        webhook_id=webhook_id,
        event_type="message.created",
        payload=payload,
        endpoint_url="https://example.com/hook",
    )

    # The worker claims it; it is now in flight, not pending.
    db.execute(
        "UPDATE webhook_retry_queue SET status = 'delivering' WHERE id = ?",
        (queued["id"],),
    )
    row = db.fetch_one(
        "SELECT metadata FROM webhook_retry_queue WHERE id = ?", (queued["id"],)
    )
    original_headers = json.loads(row["metadata"])["original_signing_headers"]
    old_public = _public_key(db, webhook_id)
    assert _signature_verifies(old_public, original_headers, payload)

    # Admin rotates the key while the row is in flight.
    manager.rotate_signing_key(webhook_id, setup["owner"].id)
    new_public = _public_key(db, webhook_id)
    assert new_public != old_public
    assert not _signature_verifies(new_public, original_headers, payload)

    # That worker dies. Backdate the stuck row past the retry window and let
    # the real cleanup path re-claim it to 'pending' for another worker.
    stale_ts = int(time.time() * 1000) - WebhookRetryQueue.RETRY_WINDOW_MS - 60_000
    db.execute(
        "UPDATE webhook_retry_queue SET updated_at = ? WHERE id = ?",
        (stale_ts, queued["id"]),
    )
    assert queue.cleanup_expired() >= 1
    reclaimed = db.fetch_one(
        "SELECT status FROM webhook_retry_queue WHERE id = ?", (queued["id"],)
    )
    assert reclaimed["status"] == "pending"

    # The re-claimed attempt must be re-signed with the NEW key, never POSTed
    # with a signature made by the retired one.
    pending = queue.get_pending_retries(limit=10)
    assert [r["id"] for r in pending] == [queued["id"]]
    client = _CapturingClient()
    deliverer = WebhookDeliverer(client=client, signer=manager.sign_payload)
    assert deliverer.deliver(pending[0]) == 200

    sent = client.headers
    assert _signature_verifies(new_public, sent, payload), (
        "re-claimed delivery still carries a signature from the retired key"
    )
    assert not _signature_verifies(old_public, sent, payload)
