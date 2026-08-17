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


def test_http_transport_posts_signed_body(monkeypatch):
    pj = _FakePlexiJoin()
    t = HttpFederationTransport(pj)

    captured = {}

    class _FakeResponse:
        status_code = 200

    class _FakeClient:
        def __init__(self, timeout=5.0):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, content=None, headers=None):
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            return _FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    # Synchronously invoke the private retry loop (no background thread).
    t._post_with_retry(
        "https://peer.example",
        "k1",
        {"artifact_id": 1, "op": {"op_type": "edit"}},
        "/api/v1/federation/artifact-op",
    )

    assert captured["url"] == "https://peer.example/api/v1/federation/artifact-op"
    body = captured["content"]
    assert json.loads(body.decode())["artifact_id"] == 1
    sig = captured["headers"]["x-plexijoin-signature"]
    assert verify_payload_signature("k1", body, sig) is True
