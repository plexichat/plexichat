"""Regression: deleting a webhook must cancel its queued outbound deliveries.

The background delivery worker drains ``webhook_retry_queue`` rows using the
endpoint URL and signing headers captured at enqueue time. It does NOT re-read
the webhook row, so a queued delivery outlives the webhook unless deletion
explicitly revokes those rows. Without that, an admin who deletes a webhook
(e.g. after its endpoint is compromised) still sees event payloads POSTed to
the old endpoint on the next delivery cycle -- a revoked webhook keeps
delivering. This test drives the real manager + retry queue and fails if any
queued row survives the delete.
"""

import pytest

from src.core.webhooks.retry import WebhookRetryQueue

pytestmark = [pytest.mark.security, pytest.mark.unit]


def test_deleting_webhook_cancels_queued_deliveries(webhook_with_token):
    setup = webhook_with_token
    db = setup["db"]
    webhook = setup["webhook"]

    queue = WebhookRetryQueue(db)
    queued = queue.enqueue(
        webhook_id=webhook.id,
        event_type="message.created",
        payload={"content": "sensitive event payload"},
        endpoint_url="https://example.com/hook",
    )

    # The delivery is genuinely queued for this webhook before the delete.
    assert queue.get_stats(webhook_id=webhook.id)["pending"] == 1
    assert [r["id"] for r in queue.get_pending_retries(limit=10)] == [queued["id"]]

    # Revocation: the admin deletes the webhook (which also claims delivery).
    assert setup["webhooks"].delete_webhook(setup["owner"].id, webhook.id) is True

    # No queued row may remain claimable: a revoked webhook must not fire.
    assert queue.get_stats(webhook_id=webhook.id)["total"] == 0
    assert [r["id"] for r in queue.get_pending_retries(limit=10)] == []


def test_deleting_webhook_cancels_retrying_and_dead_letter_rows(webhook_with_token):
    """All non-delivered states (retrying, dead_letter) are revoked too."""
    setup = webhook_with_token
    db = setup["db"]
    webhook = setup["webhook"]

    queue = WebhookRetryQueue(db)
    first = queue.enqueue(
        webhook_id=webhook.id,
        event_type="message.created",
        payload={"content": "first"},
        endpoint_url="https://example.com/hook",
    )
    queue.enqueue(
        webhook_id=webhook.id,
        event_type="message.updated",
        payload={"content": "second"},
        endpoint_url="https://example.com/hook",
    )
    queue.mark_failure(first["id"], 500, "upstream error")
    assert queue.get_stats(webhook_id=webhook.id)["retrying"] == 1

    assert setup["webhooks"].delete_webhook(setup["owner"].id, webhook.id) is True

    stats = queue.get_stats(webhook_id=webhook.id)
    assert stats["total"] == 0
    assert stats["retrying"] == 0
    assert stats["pending"] == 0
    assert [r["id"] for r in queue.get_pending_retries(limit=10)] == []
