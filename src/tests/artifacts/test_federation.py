"""Tests for the shipped artifact federation transport (PlexiJoin).

Covers the HMAC signature primitive, the real HTTP transport's connection
resolution and payload signing, and the bridge's forward path (traffic
accounting + transport invocation) without touching the network.
"""

from __future__ import annotations

import json

from src.core.artifacts.federation import (
    FederationArtifactBridge,
    HttpFederationTransport,
    _sign_payload,
    verify_payload_signature,
)


def test_sign_payload_round_trip():
    key = "shared-secret"
    body = b'{"artifact_id": 1}'
    sig = _sign_payload(key, body)
    assert verify_payload_signature(key, body, sig) is True


def test_verify_rejects_wrong_key_and_signature():
    body = b"payload"
    sig = _sign_payload("correct-key", body)
    assert verify_payload_signature("wrong-key", body, sig) is False
    assert verify_payload_signature("correct-key", body, "deadbeef") is False
    assert verify_payload_signature(None, body, sig) is False
    assert verify_payload_signature("correct-key", body, None) is False


class _FakePlexiJoin:
    def __init__(self):
        self.connections = {
            1: {"id": 1, "remote_url": "https://peer.example", "shared_key": "k1"},
            2: {"id": 2, "remote_url": "https://peer2.example", "shared_key": "k2"},
        }
        self.traffic = []

    def list_connections(self, status="active"):
        return {
            "connections": list(self.connections.values()),
            "total": len(self.connections),
        }

    def get_connection(self, connection_id):
        return self.connections.get(connection_id)

    def record_traffic(self, connection_id, direction, message_count):
        self.traffic.append((connection_id, direction, message_count))


class _FakeDb:
    def __init__(self, server_id):
        self.server_id = server_id


class _FakeArtifact:
    def __init__(self, server_id):
        self.server_id = server_id


def _patch_get_artifact(monkeypatch, server_id):
    from src.core.artifacts import federation as fed

    monkeypatch.setattr(
        fed, "get_artifact", lambda db, artifact_id: _FakeArtifact(server_id)
    )


def test_bridge_defaults_to_http_transport(monkeypatch):
    pj = _FakePlexiJoin()
    bridge = FederationArtifactBridge(None, pj)
    assert isinstance(bridge._transport, HttpFederationTransport)


def test_bridge_forward_op_invokes_transport_and_accounts(monkeypatch):
    pj = _FakePlexiJoin()
    _patch_get_artifact(monkeypatch, 5)
    bridge = FederationArtifactBridge(_FakeDb(5), pj)

    delivered = []

    def fake_transport(artifact_id, payload, actor_id, connection_ids):
        delivered.append((artifact_id, payload, actor_id, connection_ids))

    bridge.set_federation_transport(fake_transport)

    n = bridge.forward_artifact_op(42, {"op_type": "edit"}, actor_id=9)
    assert n == 2
    assert len(delivered) == 1
    assert delivered[0][0] == 42
    assert delivered[0][1]["op"] == {"op_type": "edit"}
    assert delivered[0][2] == 9
    assert delivered[0][3] == [1, 2]
    # Traffic was recorded against each connection.
    assert sorted(pj.traffic) == [(1, "outbound", 1), (2, "outbound", 1)]


def test_bridge_forward_skips_missing_artifact(monkeypatch):
    pj = _FakePlexiJoin()
    # get_artifact returns None => nothing to forward.
    from src.core.artifacts import federation as fed

    monkeypatch.setattr(fed, "get_artifact", lambda db, artifact_id: None)
    bridge = FederationArtifactBridge(_FakeDb(5), pj)
    assert bridge.forward_artifact_op(42, {"op_type": "edit"}, actor_id=9) == 0
    assert pj.traffic == []


def _patch_resolution(monkeypatch, resolved_ip="203.0.113.7"):
    """Stub only DNS; the pinned-target builder runs for real."""
    from src.core.artifacts import federation as fed

    resolutions = []

    def _resolve(url):
        resolutions.append(url)
        return "peer.example", resolved_ip

    monkeypatch.setattr(fed, "resolve_remote_target", _resolve)
    return resolutions


class _ScriptedClient:
    """Stands in for httpx.Client and records every request it is given."""

    def __init__(self, statuses=(200,)):
        self.statuses = list(statuses)
        self.calls = []

    def __call__(self, timeout=5.0):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None, extensions=None):
        self.calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "extensions": extensions,
            }
        )
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

        class _Response:
            status_code = status

        return _Response()


def test_http_transport_posts_to_the_validated_address(monkeypatch):
    """Delivery must connect to the IP the validator cleared, not the name.

    The stored URL is re-resolved by httpx if it is posted as-is, so a
    rebinding peer passes validation as public and then answers with a
    loopback address for the request. The Host header and the TLS SNI carry
    the real name so virtual hosts and certificate checks still work.
    """
    pj = _FakePlexiJoin()
    t = HttpFederationTransport(pj)
    resolutions = _patch_resolution(monkeypatch)

    client = _ScriptedClient()
    import httpx

    monkeypatch.setattr(httpx, "Client", client)

    t._post_with_retry(
        "https://peer.example",
        "k1",
        {"artifact_id": 1, "op": {"op_type": "edit"}},
        "/api/v1/federation/artifact-op",
    )

    assert resolutions == ["https://peer.example/api/v1/federation/artifact-op"]
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert sent["url"] == "https://203.0.113.7:443/api/v1/federation/artifact-op"
    assert sent["headers"]["Host"] == "peer.example"
    assert sent["extensions"]["sni_hostname"] == "peer.example"


def test_http_transport_refuses_a_blocked_remote(monkeypatch):
    from src.core.artifacts import federation as fed

    def _blocked(url):
        raise fed.RemoteURLBlockedError("remote_url blocked: loopback")

    monkeypatch.setattr(fed, "resolve_remote_target", _blocked)
    client = _ScriptedClient()
    import httpx

    monkeypatch.setattr(httpx, "Client", client)

    HttpFederationTransport(_FakePlexiJoin())._post_with_retry(
        "https://127.0.0.1:8443",
        "k1",
        {"artifact_id": 1},
        "/api/v1/federation/artifact-op",
    )

    assert client.calls == []


def test_http_transport_retries_a_rejected_delivery(monkeypatch):
    """A 5xx is not a delivery: it must retry, then report failure."""
    from src.core.artifacts import federation as fed

    _patch_resolution(monkeypatch)
    client = _ScriptedClient(statuses=(503, 502, 500))
    import httpx

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(fed.time, "sleep", lambda _s: None)

    HttpFederationTransport(_FakePlexiJoin(), max_retries=2)._post_with_retry(
        "https://peer.example",
        "k1",
        {"artifact_id": 1},
        "/api/v1/federation/artifact-op",
    )

    # max_retries=2 means three attempts, and it stops retrying a success.
    assert len(client.calls) == 3


def test_http_transport_stops_after_a_successful_retry(monkeypatch):
    from src.core.artifacts import federation as fed

    _patch_resolution(monkeypatch)
    client = _ScriptedClient(statuses=(500, 200))
    import httpx

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(fed.time, "sleep", lambda _s: None)

    HttpFederationTransport(_FakePlexiJoin(), max_retries=2)._post_with_retry(
        "https://peer.example",
        "k1",
        {"artifact_id": 1},
        "/api/v1/federation/artifact-op",
    )

    assert len(client.calls) == 2


def test_http_transport_posts_signed_body(monkeypatch):
    pj = _FakePlexiJoin()
    t = HttpFederationTransport(pj)
    _patch_resolution(monkeypatch)

    client = _ScriptedClient()
    import httpx

    monkeypatch.setattr(httpx, "Client", client)

    # Synchronously invoke the private retry loop (no background thread).
    t._post_with_retry(
        "https://peer.example",
        "k1",
        {"artifact_id": 1, "op": {"op_type": "edit"}},
        "/api/v1/federation/artifact-op",
    )

    assert len(client.calls) == 1
    captured = client.calls[0]
    body = captured["content"]
    assert json.loads(body.decode())["artifact_id"] == 1
    headers = captured["headers"]
    sig = headers["x-plexijoin-signature"]
    assert headers["x-plexijoin-timestamp"].isdigit()
    assert headers["x-plexijoin-nonce"]
    assert (
        verify_payload_signature(
            "k1",
            body,
            sig,
            headers["x-plexijoin-timestamp"],
            headers["x-plexijoin-nonce"],
        )
        is True
    )
