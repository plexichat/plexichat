"""Regression tests for deep webhook-delivery SSRF / replay hardening.

Two attack classes are pinned here:

* an injected HTTP client with ``follow_redirects=True`` must not be able to
  follow a redirect itself -- that would bypass the manual SSRF re-validation
  and IP re-pinning performed for every hop in ``WebhookDeliverer.deliver``;
* the replay-cache check-and-consume sequence must be atomic inside a process
  so two concurrent deliveries of the same captured signature cannot both be
  accepted inside the replay window.
"""

import socket
import threading
import time

import pytest

from src.core.webhooks.delivery import WebhookDeliverer, WebhookDeliveryError

PUBLIC_IP = "93.184.216.34"
_REAL_GETADDRINFO = socket.getaddrinfo


class _Response:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def test_injected_auto_follow_client_cannot_bypass_redirect_ssrf_gate(monkeypatch):
    """A client that follows redirects itself must be forcibly unfollowed.

    If the HTTP client transparently follows a 302, the redirect never reaches
    ``deliver``'s SSRF gate and a public endpoint can bounce the delivery to an
    internal address. The deliverer therefore disables client-side auto-follow
    even on an injected client before issuing any request.
    """
    internal = "http://169.254.169.254/latest/meta-data/"

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (PUBLIC_IP, 0),
                )
            ]
        # Let IP literals resolve normally so the metadata address is seen as
        # the private/reserved target the validator is meant to reject.
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)

    class _AutoFollowClient:
        """Mimics httpx with follow_redirects=True: follows 302s internally."""

        def __init__(self):
            self.follow_redirects = True
            self.calls = []

        def post(self, url, json=None, headers=None, extensions=None):
            self.calls.append(url)
            if self.follow_redirects:
                # httpx would fetch the Location with no SSRF validation at
                # all and hand us the final 200 -- the exact bypass we fear.
                return _Response(200)
            return _Response(302, {"Location": internal})

    client = _AutoFollowClient()
    deliverer = WebhookDeliverer(client=client)

    # The deliverer must have neutralised the injected client's auto-follow.
    assert client.follow_redirects is False

    with pytest.raises(WebhookDeliveryError, match="redirect target refused"):
        deliverer.deliver(
            {
                "endpoint_url": "https://example.com/hook",
                "webhook_id": 1,
                "payload": {"a": 1},
            }
        )
    # The internal target must never have been POSTed to.
    assert len(client.calls) == 1


def test_concurrent_replay_cannot_be_accepted_twice(webhook_with_token, monkeypatch):
    """The replay check and 'consumed' write form one atomic critical section.

    Before the fix, two threads presenting the same captured signature could
    both observe a cache miss before either wrote the marker, so both passed
    verification inside the replay window. The widened ``cache_get`` below
    makes that race deterministic; with the process lock in place the second
    thread sees the marker and is rejected.
    """
    setup = webhook_with_token
    manager = setup["webhooks"]
    webhook_id = setup["webhook"].id
    payload = {"event": "concurrent-replay"}
    headers = manager.sign_payload(webhook_id, payload)
    signature = headers["X-Plexichat-Signature"]
    timestamp = headers["X-Plexichat-Timestamp"]

    store = {}
    real_sleep = time.sleep

    def fake_cache_get(key):
        # Capture the miss FIRST, then widen the check -> set window. With an
        # unguarded verifier both callers capture "not seen" before either
        # writes the marker, so both are accepted; with the lock the second
        # caller only reads the cache after the first has marked the pair.
        missed = store.get(key)
        real_sleep(0.05)
        return missed

    def fake_cache_set(key, value, ttl=None):
        store[key] = value
        return True

    import src.core.database.cache as cache_module

    monkeypatch.setattr(cache_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(cache_module, "cache_set", fake_cache_set)

    results = []
    errors = []

    def worker():
        try:
            results.append(
                manager.verify_payload(webhook_id, payload, signature, timestamp)
            )
        except Exception as exc:  # pragma: no cover - surface thread errors
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    assert sorted(results) == [False, True], results
