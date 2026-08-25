"""
Tests for the outbound webhook delivery engine (delivery.py).

The engine turns due retry-queue records into HTTP calls and drives the
queue to delivered / retrying / dead-letter states. HTTP is faked via an
injectable client so no real network is involved.
"""

import threading
import time

import pytest

from src.core.webhooks.retry import WebhookRetryQueue
from src.core.webhooks.delivery import (
    SIGNATURE_STALE_AFTER_MS,
    WebhookDeliverer,
    WebhookDeliveryError,
    WebhookDeliveryJob,
    process_due_retries,
)


def _fresh_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _ScriptedClient:
    """Fake HTTP client returning a scripted sequence of status codes."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        status = self.statuses.pop(0) if self.statuses else 200
        return _FakeResponse(status)


class _RaisingClient:
    def post(self, url, json=None, headers=None):
        raise ConnectionError("connection refused")


@pytest.fixture
def retry_queue(db):
    return WebhookRetryQueue(db)


def _enqueue(retry_queue, url="https://example.com/hook", headers=None):
    return retry_queue.enqueue(
        webhook_id=1,
        event_type="message.created",
        payload={"content": "hello"},
        endpoint_url=url,
        signing_headers=headers,
    )


class TestWebhookDeliverer:
    def test_delivers_payload_with_signing_headers(self):
        fresh_ts = _fresh_timestamp_ms()
        record = {
            "endpoint_url": "https://example.com/hook",
            "payload": {"content": "hello"},
            "metadata": {
                "original_signing_headers": {
                    "X-Plexichat-Signature": "sig",
                    "X-Plexichat-Timestamp": fresh_ts,
                }
            },
        }
        client = _ScriptedClient([200])
        deliverer = WebhookDeliverer(client=client)

        assert deliverer.deliver(record) == 200
        call = client.calls[0]
        assert call["url"] == "https://example.com/hook"
        assert call["json"] == {"content": "hello"}
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["headers"]["X-Plexichat-Signature"] == "sig"
        assert call["headers"]["X-Plexichat-Timestamp"] == fresh_ts

    def test_deliver_without_signing_headers_sends_content_type_only(self):
        record = {
            "endpoint_url": "https://example.com/hook",
            "payload": {"a": 1},
            "metadata": None,
        }
        client = _ScriptedClient([204])
        deliverer = WebhookDeliverer(client=client)

        assert deliverer.deliver(record) == 204
        headers = client.calls[0]["headers"]
        assert headers == {"Content-Type": "application/json"}

    def test_deliver_missing_endpoint_url_raises(self):
        deliverer = WebhookDeliverer(client=_ScriptedClient([]))
        with pytest.raises(WebhookDeliveryError, match="endpoint_url"):
            deliverer.deliver({"payload": {"a": 1}})

    def test_deliver_non_dict_payload_raises(self):
        deliverer = WebhookDeliverer(client=_ScriptedClient([]))
        with pytest.raises(WebhookDeliveryError, match="payload"):
            deliverer.deliver(
                {"endpoint_url": "https://example.com/x", "payload": "not-a-dict"}
            )

    def test_deliver_wraps_transport_errors(self):
        deliverer = WebhookDeliverer(client=_RaisingClient())
        with pytest.raises(WebhookDeliveryError, match="connection refused"):
            deliverer.deliver(
                {"endpoint_url": "https://example.com/x", "payload": {"a": 1}}
            )

    def test_deliver_refuses_private_endpoint(self):
        """F1: loopback/private endpoints must never be POSTed to."""
        deliverer = WebhookDeliverer(client=_ScriptedClient([200]))
        with pytest.raises(WebhookDeliveryError, match="SSRF"):
            deliverer.deliver(
                {
                    "endpoint_url": "http://127.0.0.1:9/hook",
                    "payload": {"a": 1},
                }
            )
        assert not _ScriptedClient([200]).calls

    def test_deliver_refuses_disallowed_scheme(self):
        deliverer = WebhookDeliverer(client=_ScriptedClient([200]))
        with pytest.raises(WebhookDeliveryError, match="SSRF"):
            deliverer.deliver(
                {"endpoint_url": "file:///etc/passwd", "payload": {"a": 1}}
            )

    def test_stale_signature_refreshed_by_signer(self):
        """F17: frozen signatures older than the replay window are re-signed."""
        captured = {}

        class _CaptureClient:
            def post(self, url, json=None, headers=None):
                captured["headers"] = headers
                return _FakeResponse(200)

        def signer(webhook_id, payload):
            return {
                "X-Plexichat-Signature": "fresh-sig",
                "X-Plexichat-Timestamp": _fresh_timestamp_ms(),
            }

        deliverer = WebhookDeliverer(client=_CaptureClient(), signer=signer)
        stale_ts = str(int(time.time() * 1000) - SIGNATURE_STALE_AFTER_MS - 1000)
        status = deliverer.deliver(
            {
                "endpoint_url": "https://example.com/hook",
                "webhook_id": 7,
                "payload": {"a": 1},
                "metadata": {
                    "original_signing_headers": {
                        "X-Plexichat-Signature": "stale-sig",
                        "X-Plexichat-Timestamp": stale_ts,
                    }
                },
            }
        )
        assert status == 200
        assert captured["headers"]["X-Plexichat-Signature"] == "fresh-sig"
        assert captured["headers"]["X-Plexichat-Timestamp"] != stale_ts

    def test_stale_signature_without_signer_delivers_unsigned(self):
        captured = {}

        class _CaptureClient:
            def post(self, url, json=None, headers=None):
                captured["headers"] = headers
                return _FakeResponse(200)

        deliverer = WebhookDeliverer(client=_CaptureClient())
        stale_ts = str(int(time.time() * 1000) - SIGNATURE_STALE_AFTER_MS - 1000)
        deliverer.deliver(
            {
                "endpoint_url": "https://example.com/hook",
                "webhook_id": 7,
                "payload": {"a": 1},
                "metadata": {
                    "original_signing_headers": {
                        "X-Plexichat-Signature": "stale-sig",
                        "X-Plexichat-Timestamp": stale_ts,
                    }
                },
            }
        )
        assert "X-Plexichat-Signature" not in captured["headers"]
        assert "X-Plexichat-Timestamp" not in captured["headers"]


class _StubDeliverer:
    """Deliverer whose responses are scripted per record id."""

    def __init__(self, responses):
        self.responses = responses  # record_id -> int or exception
        self.seen = []

    def deliver(self, record):
        self.seen.append(record["id"])
        outcome = self.responses.get(record["id"], 200)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestProcessDueRetries:
    def test_success_marks_delivered(self, db, retry_queue):
        record = _enqueue(retry_queue)
        deliverer = _StubDeliverer({record["id"]: 200})

        stats = process_due_retries(retry_queue, deliverer)

        assert stats == {
            "processed": 1,
            "delivered": 1,
            "failed": 0,
            "transport_errors": 0,
        }
        row = db.fetch_one(
            "SELECT status, response_status, delivered_at FROM webhook_retry_queue WHERE id = ?",
            (record["id"],),
        )
        assert row["status"] == "delivered"
        assert row["response_status"] == 200
        assert row["delivered_at"] is not None

    def test_http_error_schedules_retry(self, db, retry_queue):
        record = _enqueue(retry_queue)
        deliverer = _StubDeliverer({record["id"]: 500})

        stats = process_due_retries(retry_queue, deliverer)

        assert stats["processed"] == 1
        assert stats["failed"] == 1
        assert stats["delivered"] == 0
        row = db.fetch_one(
            "SELECT status, last_error, attempt_count FROM webhook_retry_queue WHERE id = ?",
            (record["id"],),
        )
        assert row["status"] == "retrying"
        assert row["last_error"] == "HTTP 500"
        assert row["attempt_count"] == 1

    def test_transport_error_marks_failure(self, db, retry_queue):
        record = _enqueue(retry_queue)
        deliverer = _StubDeliverer(
            {record["id"]: WebhookDeliveryError("connection refused")}
        )

        stats = process_due_retries(retry_queue, deliverer)

        assert stats["transport_errors"] == 1
        assert stats["failed"] == 1
        row = db.fetch_one(
            "SELECT status, last_error FROM webhook_retry_queue WHERE id = ?",
            (record["id"],),
        )
        assert row["status"] == "retrying"
        assert row["last_error"] == "connection refused"

    def test_unexpected_deliverer_exception_survives_loop(self, db, retry_queue):
        record = _enqueue(retry_queue)
        deliverer = _StubDeliverer({record["id"]: RuntimeError("boom")})

        stats = process_due_retries(retry_queue, deliverer)

        assert stats["transport_errors"] == 1
        assert stats["failed"] == 1
        row = db.fetch_one(
            "SELECT status, last_error FROM webhook_retry_queue WHERE id = ?",
            (record["id"],),
        )
        assert row["status"] == "retrying"
        assert row["last_error"] == "delivery error"

    def test_repeated_failures_dead_letter(self, db, retry_queue):
        record = _enqueue(retry_queue)
        deliverer = _StubDeliverer({record["id"]: 500})

        for _ in range(WebhookRetryQueue.MAX_RETRIES):
            stats = process_due_retries(retry_queue, deliverer)
            assert stats["processed"] == 1
            # Simulate the backoff window elapsing so the record is due
            # again on the next scheduler pass.
            db.execute(
                "UPDATE webhook_retry_queue SET next_retry_at = 0 WHERE id = ?",
                (record["id"],),
            )

        row = db.fetch_one(
            "SELECT status FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "dead_letter"

    def test_mixed_statuses(self, db, retry_queue):
        ok = _enqueue(retry_queue, url="https://example.com/ok")
        bad = _enqueue(retry_queue, url="https://example.com/bad")
        deliverer = _StubDeliverer({ok["id"]: 200, bad["id"]: 503})

        stats = process_due_retries(retry_queue, deliverer)

        assert stats["processed"] == 2
        assert stats["delivered"] == 1
        assert stats["failed"] == 1
        assert stats["transport_errors"] == 0
        assert set(deliverer.seen) == {ok["id"], bad["id"]}

    def test_future_retry_not_processed(self, db, retry_queue):
        record = _enqueue(retry_queue)
        db.execute(
            "UPDATE webhook_retry_queue SET next_retry_at = ? WHERE id = ?",
            (9_000_000_000_000, record["id"]),
        )
        deliverer = _StubDeliverer({})

        stats = process_due_retries(retry_queue, deliverer)

        assert stats["processed"] == 0
        assert deliverer.seen == []

    def test_limit_respected(self, db, retry_queue):
        first = _enqueue(retry_queue)
        second = _enqueue(retry_queue)
        deliverer = _StubDeliverer({first["id"]: 200, second["id"]: 200})

        stats = process_due_retries(retry_queue, deliverer, limit=1)

        assert stats["processed"] == 1
        assert stats["delivered"] == 1
        assert len(deliverer.seen) == 1

    def test_default_deliverer_built_when_none(self, retry_queue):
        deliverer = WebhookDeliverer()
        assert deliverer._timeout == 10.0


class TestWebhookDeliveryJob:
    def test_interval_default_and_config(self):
        assert WebhookDeliveryJob(None)._interval_seconds() == 30
        assert (
            WebhookDeliveryJob(
                None, {"delivery_interval_seconds": 5}
            )._interval_seconds()
            == 5
        )
        assert (
            WebhookDeliveryJob(
                None, {"delivery_interval_seconds": "not-a-number"}
            )._interval_seconds()
            == 30
        )
        assert (
            WebhookDeliveryJob(
                None, {"delivery_interval_seconds": 0}
            )._interval_seconds()
            is None
        )
        assert (
            WebhookDeliveryJob(
                None, {"delivery_interval_seconds": -3}
            )._interval_seconds()
            is None
        )

    def test_start_without_db_skips(self):
        job = WebhookDeliveryJob(None)
        job.start()
        assert job._is_running is False
        assert job._thread is None

    def test_start_disabled_interval_skips(self, db):
        job = WebhookDeliveryJob(db, {"delivery_interval_seconds": 0})
        job.start()
        assert job._is_running is False
        assert job._thread is None

    def test_run_once_delivers_due_records(self, db, retry_queue):
        record = _enqueue(retry_queue)
        job = WebhookDeliveryJob(db, {"delivery_interval_seconds": 30})

        # Route run_once through a scripted deliverer instead of the network.
        import src.core.webhooks.delivery as delivery_module

        deliverer = _StubDeliverer({record["id"]: 200})
        original = delivery_module.WebhookDeliverer
        delivery_module.WebhookDeliverer = lambda **kwargs: deliverer
        try:
            stats = job.run_once()
        finally:
            delivery_module.WebhookDeliverer = original

        assert stats["processed"] == 1
        assert stats["delivered"] == 1
        row = db.fetch_one(
            "SELECT status FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "delivered"

    def test_run_once_isolates_bad_records(self, db, retry_queue):
        # A record whose delivery raises must not stop the cycle.
        bad = _enqueue(retry_queue, url="https://example.com/bad")
        good = _enqueue(retry_queue, url="https://example.com/good")
        job = WebhookDeliveryJob(db, {"delivery_interval_seconds": 30})

        import src.core.webhooks.delivery as delivery_module

        deliverer = _StubDeliverer(
            {
                bad["id"]: WebhookDeliveryError("downstream timeout"),
                good["id"]: 200,
            }
        )
        original = delivery_module.WebhookDeliverer
        delivery_module.WebhookDeliverer = lambda **kwargs: deliverer
        try:
            stats = job.run_once()
        finally:
            delivery_module.WebhookDeliverer = original

        assert stats["processed"] == 2
        assert stats["delivered"] == 1
        assert stats["failed"] == 1

    def test_start_and_stop_thread_lifecycle(self, db, monkeypatch):
        job = WebhookDeliveryJob(db, {"delivery_interval_seconds": 1})
        cycles = []

        def fake_run_once():
            cycles.append(time.time())
            return {"processed": 0, "delivered": 0, "failed": 0, "transport_errors": 0}

        monkeypatch.setattr(job, "run_once", fake_run_once)
        job.start()
        assert job._is_running is True
        assert isinstance(job._thread, threading.Thread)
        assert job._thread.name == "WebhookDeliveryJob"
        assert job._thread.daemon is True

        deadline = time.time() + 3
        while len(cycles) < 2 and time.time() < deadline:
            time.sleep(0.05)

        job.stop()
        assert job._is_running is False
        assert job._thread is None
        assert len(cycles) >= 2

    def test_stop_without_start_is_safe(self):
        job = WebhookDeliveryJob(None)
        job.stop()
        assert job._is_running is False


class TestRealHttpEndpointDelivery:
    """Full-stack delivery against a real local HTTP server.

    These tests bind a real ``ThreadingHTTPServer`` on an ephemeral port and
    run the actual httpx-backed ``WebhookDeliverer``, so the HTTP path
    (headers, JSON body, status handling) is exercised end-to-end rather
    than through fakes.
    """

    @staticmethod
    def _start_server(handler_cls):
        from http.server import ThreadingHTTPServer
        import threading

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @staticmethod
    def _stop_server(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def test_delivers_to_real_endpoint_and_marks_success(self, db, retry_queue):
        import json
        from http.server import BaseHTTPRequestHandler

        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received["path"] = self.path
                received["content_type"] = self.headers.get("Content-Type")
                received["signature"] = self.headers.get("X-Plexichat-Signature")
                received["payload"] = json.loads(body)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *args):  # silence request logging
                pass

        server, thread = self._start_server(Handler)
        try:
            port = server.server_address[1]
            record = retry_queue.enqueue(
                webhook_id=7,
                event_type="message.created",
                payload={"content": "real payload", "n": 1},
                endpoint_url=f"http://127.0.0.1:{port}/hooks/endpoint",
                allow_private_network=True,
                signing_headers={
                    "X-Plexichat-Signature": "ed25519:sig",
                    "X-Plexichat-Timestamp": _fresh_timestamp_ms(),
                },
            )

            stats = process_due_retries(
                retry_queue, WebhookDeliverer(allow_private_network=True)
            )

            assert stats == {
                "processed": 1,
                "delivered": 1,
                "failed": 0,
                "transport_errors": 0,
            }
            assert received["path"] == "/hooks/endpoint"
            assert received["content_type"] == "application/json"
            assert received["signature"] == "ed25519:sig"
            assert received["payload"] == {"content": "real payload", "n": 1}

            row = db.fetch_one(
                "SELECT status, response_status, delivered_at "
                "FROM webhook_retry_queue WHERE id = ?",
                (record["id"],),
            )
            assert row["status"] == "delivered"
            assert row["response_status"] == 200
            assert row["delivered_at"] is not None
        finally:
            self._stop_server(server, thread)

    def test_real_endpoint_http_500_schedules_retry(self, db, retry_queue):
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")

            def log_message(self, *args):
                pass

        server, thread = self._start_server(Handler)
        try:
            port = server.server_address[1]
            record = retry_queue.enqueue(
                webhook_id=8,
                event_type="message.created",
                payload={"content": "x"},
                endpoint_url=f"http://127.0.0.1:{port}/hooks/fail",
                allow_private_network=True,
            )

            stats = process_due_retries(
                retry_queue, WebhookDeliverer(allow_private_network=True)
            )

            assert stats["processed"] == 1
            assert stats["failed"] == 1
            assert stats["delivered"] == 0
            row = db.fetch_one(
                "SELECT status, response_status, last_error FROM webhook_retry_queue "
                "WHERE id = ?",
                (record["id"],),
            )
            assert row["status"] == "retrying"
            assert row["response_status"] == 500
            assert row["last_error"] == "HTTP 500"
        finally:
            self._stop_server(server, thread)

    def test_real_endpoint_404_dead_letters_after_budget(self, db, retry_queue):
        from http.server import BaseHTTPRequestHandler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"missing")

            def log_message(self, *args):
                pass

        server, thread = self._start_server(Handler)
        try:
            port = server.server_address[1]
            record = retry_queue.enqueue(
                webhook_id=9,
                event_type="message.created",
                payload={"content": "x"},
                endpoint_url=f"http://127.0.0.1:{port}/hooks/never",
                allow_private_network=True,
            )

            for _ in range(WebhookRetryQueue.MAX_RETRIES):
                process_due_retries(
                    retry_queue, WebhookDeliverer(allow_private_network=True)
                )
                db.execute(
                    "UPDATE webhook_retry_queue SET next_retry_at = 0 WHERE id = ?",
                    (record["id"],),
                )

            row = db.fetch_one(
                "SELECT status FROM webhook_retry_queue WHERE id = ?",
                (record["id"],),
            )
            assert row["status"] == "dead_letter"
        finally:
            self._stop_server(server, thread)

    def test_connection_refused_is_transport_error(self, db, retry_queue):
        """A port with no listener must be counted as a transport error."""
        import socket

        # Grab an ephemeral port and immediately close it so nothing listens.
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        record = retry_queue.enqueue(
            webhook_id=10,
            event_type="message.created",
            payload={"content": "x"},
            endpoint_url=f"http://127.0.0.1:{port}/hooks/refused",
            allow_private_network=True,
        )

        stats = process_due_retries(
            retry_queue, WebhookDeliverer(allow_private_network=True)
        )

        assert stats["processed"] == 1
        assert stats["transport_errors"] == 1
        assert stats["delivered"] == 0
        row = db.fetch_one(
            "SELECT status FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "retrying"
