"""Regression tests for the webhook delivery security boundary.

Four attack classes are pinned here, each at the layer that actually enforces
the defence:

* unsigned / partially-signed inbound deliveries are refused, and a properly
  signed one is verified through the module-level API the route uses;
* a captured signature cannot be replayed, and oversized headers/payloads are
  rejected before they reach the verifier or the wire;
* the SSRF gate covers enqueue, delivery and every redirect hop;
* credentials, hop-by-hop and proxy-routing headers never leave the delivery
  engine, even when they arrive in tampered retry metadata.
"""

import time
import uuid

import pytest

from src.core import webhooks as webhooks_module
from src.core.webhooks.delivery import (
    MAX_DELIVERY_PAYLOAD_BYTES,
    MAX_REDIRECTS,
    WebhookDeliverer,
    WebhookDeliveryError,
)
from src.core.webhooks.retry import WebhookRetryQueue
from src.core.webhooks.url_safety import validate_delivery_endpoint


# ---------------------------------------------------------------------------
# Helpers / doubles
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


class _Client:
    """Fake HTTP client with scripted responses and a full call transcript."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, extensions=None):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": dict(headers or {}),
                "extensions": extensions,
            }
        )
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _fresh_ts():
    return str(int(time.time() * 1000))


def _record(url="https://example.com/hook", payload=None, headers=None):
    record = {
        "endpoint_url": url,
        "payload": {"content": "hello"} if payload is None else payload,
        "webhook_id": 1,
    }
    if headers is not None:
        record["metadata"] = {"original_signing_headers": headers}
    return record


def _make_webhook(modules, server, require_signed):
    channels = modules.servers.get_channels(server.owner_id, server.id)
    return modules.webhooks.create_webhook(
        user_id=server.owner_id,
        channel_id=channels[0].id,
        name=f"Security Test {uuid.uuid4().hex[:8]}",
        require_signed_deliveries=require_signed,
    )


def _signed_headers(manager, webhook_id, content):
    """Sign exactly the payload the execute route reconstructs."""
    payload = {
        "content": content,
        "username": None,
        "avatar_url": None,
        "embeds": None,
        "thread_id": None,
    }
    return manager.sign_payload(webhook_id, payload)


# ---------------------------------------------------------------------------
# Unsigned / partially signed / replayed inbound deliveries
# ---------------------------------------------------------------------------


class TestInboundSignatureEnforcement:
    def test_unsigned_delivery_rejected_when_required(self, test_client, modules, test_server):
        webhook = _make_webhook(modules, test_server[0], require_signed=True)
        response = test_client.post(
            f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
            json={"content": "unsigned regression"},
        )
        assert response.status_code == 401

    def test_partial_signature_pair_is_rejected(self, test_client, modules, test_server):
        """Supplying only one of the two headers must never downgrade to unsigned."""
        webhook = _make_webhook(modules, test_server[0], require_signed=False)
        url = f"/api/v1/webhooks/{webhook.id}/{webhook.token}"
        assert (
            test_client.post(
                url,
                json={"content": "partial"},
                headers={"X-Plexichat-Signature": "sig"},
            ).status_code
            == 401
        )
        assert (
            test_client.post(
                url,
                json={"content": "partial"},
                headers={"X-Plexichat-Timestamp": _fresh_ts()},
            ).status_code
            == 401
        )

    def test_signed_delivery_verified_through_module_api(
        self, test_client, modules, test_server
    ):
        """A valid signature is actually verified, not silently skipped.

        Before the module-level ``sign_payload``/``verify_payload`` wrappers
        existed, the execute route's verification call raised AttributeError
        and every signed delivery failed.
        """
        webhook = _make_webhook(modules, test_server[0], require_signed=True)
        content = f"verified {uuid.uuid4().hex[:8]}"
        headers = _signed_headers(modules.webhooks, webhook.id, content)
        assert headers["X-Plexichat-Signature"]

        response = test_client.post(
            f"/api/v1/webhooks/{webhook.id}/{webhook.token}",
            json={"content": content},
            headers=headers,
        )
        assert response.status_code == 200

    def test_replayed_signed_delivery_is_rejected(
        self, test_client, modules, test_server
    ):
        webhook = _make_webhook(modules, test_server[0], require_signed=True)
        content = f"replay {uuid.uuid4().hex[:8]}"
        headers = _signed_headers(modules.webhooks, webhook.id, content)
        url = f"/api/v1/webhooks/{webhook.id}/{webhook.token}"

        assert test_client.post(url, json={"content": content}, headers=headers).status_code == 200
        # Same (signature, timestamp) pair a second time -> replay cache hit.
        assert test_client.post(url, json={"content": content}, headers=headers).status_code == 401

    def test_module_verify_wrapper_fails_closed_without_manager(self, monkeypatch):
        monkeypatch.setattr(webhooks_module, "_get_manager", lambda: (_ for _ in ()).throw(RuntimeError("unwired")))
        assert webhooks_module.verify_payload(1, {"a": 1}, "sig", _fresh_ts()) is False


# ---------------------------------------------------------------------------
# Oversized / malformed inputs
# ---------------------------------------------------------------------------


class TestSizeBounds:
    def test_oversized_payload_rejected_at_enqueue(self, db):
        queue = WebhookRetryQueue(db)
        payload = {"blob": "x" * (queue.MAX_PAYLOAD_SIZE + 1)}
        with pytest.raises(ValueError, match="maximum size"):
            queue.enqueue(
                webhook_id=1,
                event_type="message.created",
                payload=payload,
                endpoint_url="https://example.com/hook",
            )

    def test_oversized_payload_refused_at_delivery(self):
        client = _Client([_Response(200)])
        payload = {"blob": "x" * (MAX_DELIVERY_PAYLOAD_BYTES + 1)}
        with pytest.raises(WebhookDeliveryError, match="exceeds maximum delivery size"):
            WebhookDeliverer(client=client).deliver(_record(payload=payload))
        assert client.calls == [], "an oversized body must never reach the wire"

    def test_oversized_header_value_is_dropped(self):
        client = _Client([_Response(200)])
        headers = {
            "X-Plexichat-Signature": "sig",
            "X-Plexichat-Timestamp": _fresh_ts(),
            "X-Huge": "x" * 5000,
        }
        WebhookDeliverer(client=client).deliver(_record(headers=headers))
        sent = client.calls[0]["headers"]
        assert "X-Huge" not in sent
        assert sent["X-Plexichat-Signature"] == "sig"
        assert sent["X-Plexichat-Timestamp"] == headers["X-Plexichat-Timestamp"]

    def test_backoff_exponent_and_retry_budget_are_bounded(self, db):
        queue = WebhookRetryQueue(db)
        # A corrupt attempt count must not trigger an unbounded exponentiation.
        delay = queue._calculate_delay(10**6)
        assert 0 <= delay <= queue.MAX_DELAY_MS + queue.JITTER_MS

        record = queue.enqueue(
            webhook_id=1,
            event_type="message.created",
            payload={"a": 1},
            endpoint_url="https://example.com/hook",
        )
        db.execute(
            "UPDATE webhook_retry_queue SET attempt_count = ?, max_attempts = ? WHERE id = ?",
            (queue.MAX_ALLOWED_RETRIES + 5, 10**6, record["id"]),
        )
        result = queue.mark_failure(record["id"], 500, "boom")
        # Clamped counters mean the budget is exhausted -> dead letter, not an
        # endless retry loop.
        assert result["status"] == "dead_letter"


# ---------------------------------------------------------------------------
# SSRF: enqueue, delivery and redirect re-checks
# ---------------------------------------------------------------------------


class TestSSRFGate:
    def test_private_endpoint_refused_at_enqueue_and_delivery(self, db):
        queue = WebhookRetryQueue(db)
        with pytest.raises(ValueError, match="forbidden"):
            queue.enqueue(
                webhook_id=1,
                event_type="message.created",
                payload={"a": 1},
                endpoint_url="http://127.0.0.1:9/hook",
            )
        with pytest.raises(WebhookDeliveryError, match="SSRF"):
            WebhookDeliverer(client=_Client([])).deliver(
                _record("http://169.254.169.254/latest/meta-data/")
            )

    def test_private_network_seam_is_loopback_only(self):
        with pytest.raises(ValueError, match="loopback"):
            validate_delivery_endpoint("http://10.0.0.5/hook", allow_private_network=True)
        assert validate_delivery_endpoint(
            "http://127.0.0.1:9/hook", allow_private_network=True
        ) == ("127.0.0.1", "")

    def test_redirect_to_internal_target_is_refused(self):
        client = _Client(
            [
                _Response(302, {"Location": "http://169.254.169.254/latest/meta-data/"}),
            ]
        )
        with pytest.raises(WebhookDeliveryError, match="redirect target refused"):
            WebhookDeliverer(client=client).deliver(_record())
        assert len(client.calls) == 1, "the second POST must never be issued"

    def test_safe_redirect_is_revalidated_and_pinned(self):
        client = _Client(
            [
                _Response(302, {"Location": "https://second.example.com/hook"}),
                _Response(200),
            ]
        )
        assert WebhookDeliverer(client=client).deliver(_record()) == 200
        assert len(client.calls) == 2
        first, second = client.calls
        assert first["url"] == "https://93.184.216.34:443/hook"
        assert first["headers"]["Host"] == "example.com"
        assert second["url"] == "https://93.184.216.34:443/hook"
        assert second["headers"]["Host"] == "second.example.com"
        assert second["extensions"] == {"sni_hostname": "second.example.com"}

    def test_redirect_budget_is_bounded(self):
        client = _Client(
            [_Response(302, {"Location": "/next"}) for _ in range(MAX_REDIRECTS + 2)]
        )
        status = WebhookDeliverer(client=client).deliver(_record())
        assert status == 302
        assert len(client.calls) == MAX_REDIRECTS + 1


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------


class TestForwardedHeaderSanitization:
    def test_sensitive_and_hop_by_hop_headers_are_stripped(self):
        client = _Client([_Response(200)])
        headers = {
            "X-Plexichat-Signature": "sig",
            "X-Plexichat-Timestamp": _fresh_ts(),
            "Authorization": "Bearer secret",
            "Cookie": "session=secret",
            "X-Api-Key": "secret",
            "X-Forwarded-For": "127.0.0.1",
            "Connection": "X-Internal",
            "X-Internal": "never-forward",
            "Transfer-Encoding": "chunked",
            "Host": "attacker.example",
            "Content-Type": "text/plain",
        }
        WebhookDeliverer(client=client).deliver(_record(headers=headers))
        sent = client.calls[0]["headers"]

        for name in (
            "Authorization",
            "Cookie",
            "X-Api-Key",
            "X-Forwarded-For",
            "Connection",
            "X-Internal",
            "Transfer-Encoding",
        ):
            assert name.lower() not in {k.lower() for k in sent}
        assert sent["Host"] == "example.com", "stored Host must not override the pin"
        assert sent["Content-Type"] == "application/json"
        assert sent["X-Plexichat-Signature"] == "sig"
        assert sent["X-Plexichat-Timestamp"] == headers["X-Plexichat-Timestamp"]
