"""PlexiJoin federation surface — inbound pipeline, lifecycle, transport hygiene.

Covers the W6-E fixes:
- inbound request submission + ATOMIC approve/deny transitions
- connection lifecycle pending -> active (approve / activate_connection)
- SSRF validation of remote URLs
- outbound message transport wiring (traffic counters fed on success)
"""

from __future__ import annotations

import json

import pytest

from src.core.plexijoin import (
    RemoteURLBlockedError,
    validate_remote_url,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def plexijoin(db):
    from src.core.plexijoin.manager import PlexiJoinManager

    class _StubEnc:
        def encrypt(self, s):
            return "enc:" + s

        def decrypt(self, s):
            return s[len("enc:") :]

    return PlexiJoinManager(db, admin_logger=None, encryption_service=_StubEnc())


def _make_connection(plexijoin, instance_id="remote-a"):
    return plexijoin.create_connection(
        remote_instance_id=instance_id,
        remote_url="https://remote.example",
        shared_key="sekret",
        note="test",
        admin_id=1,
    )


class TestRemoteURLValidation:
    def test_rejects_non_https_scheme(self):
        with pytest.raises(RemoteURLBlockedError):
            validate_remote_url("http://remote.example")

    def test_rejects_localhost(self):
        with pytest.raises(RemoteURLBlockedError):
            validate_remote_url("https://localhost/api")

    def test_rejects_private_ip_host(self):
        with pytest.raises(RemoteURLBlockedError):
            validate_remote_url("https://127.0.0.1/api")

    def test_accepts_public_https_url(self):
        # Public IP literal: passes SSRF checks without needing DNS.
        validate_remote_url("https://1.1.1.1/api")

    def test_rejects_http_loopback_without_selftest_env(self, monkeypatch):
        monkeypatch.delenv("PLEXICHAT_SELF_TEST", raising=False)
        with pytest.raises(RemoteURLBlockedError):
            validate_remote_url("http://127.0.0.1:8443/api")

    def test_rejects_http_loopback_with_selftest_env(self, monkeypatch):
        # Federation is https-only, always: the self-test allowance covers
        # loopback resolution, never the scheme. The fake remote peer
        # serves HTTPS for exactly this reason.
        monkeypatch.setenv("PLEXICHAT_SELF_TEST", "1")
        with pytest.raises(RemoteURLBlockedError):
            validate_remote_url("http://127.0.0.1:8443/api")

    def test_selftest_env_does_not_allow_http_non_loopback(self, monkeypatch):
        monkeypatch.setenv("PLEXICHAT_SELF_TEST", "1")
        with pytest.raises(RemoteURLBlockedError):
            validate_remote_url("http://remote.example/api")


class TestInboundPipeline:
    def test_submit_then_list_pending(self, plexijoin):
        row = plexijoin.submit_inbound_request(
            remote_instance_id="remote-a",
            remote_url="https://remote-a.example",
            requested_by="42",
            note="hello",
        )
        assert row["status"] == "pending"

        listed = plexijoin.list_requests(status="pending")
        assert any(r["id"] == row["id"] for r in listed["requests"])

    def test_approve_is_atomic_single_transition(self, plexijoin):
        row = plexijoin.submit_inbound_request(
            remote_instance_id="remote-a", remote_url="https://remote-a.example"
        )
        approved = plexijoin.approve_request(row["id"], admin_id=7)
        assert approved["status"] == "approved"
        assert approved["reviewed_by"] == 7

        # Second approval can no longer win (previously both admins could).
        with pytest.raises(ValueError, match="already processed"):
            plexijoin.approve_request(row["id"], admin_id=8)

    def test_deny_after_approve_race_safe(self, plexijoin):
        row = plexijoin.submit_inbound_request(
            remote_instance_id="remote-b", remote_url="https://remote-b.example"
        )
        plexijoin.approve_request(row["id"], admin_id=7)
        with pytest.raises(ValueError, match="already processed"):
            plexijoin.deny_request(row["id"], admin_id=8)

    def test_deny_atomic(self, plexijoin):
        row = plexijoin.submit_inbound_request(
            remote_instance_id="remote-c", remote_url="https://remote-c.example"
        )
        denied = plexijoin.deny_request(row["id"], admin_id=9)
        assert denied["status"] == "denied"


class TestConnectionLifecycle:
    def test_approve_activates_matching_pending_connection(self, plexijoin):
        conn = _make_connection(plexijoin, "federated-peer")
        assert conn["status"] == "pending"

        row = plexijoin.submit_inbound_request(
            remote_instance_id="federated-peer",
            remote_url="https://remote.example",
        )
        plexijoin.approve_request(row["id"], admin_id=1)

        refreshed = plexijoin.get_connection(conn["id"])
        assert refreshed["status"] == "active"
        assert refreshed["connected_at"] is not None

    def test_activate_connection_idempotent(self, plexijoin):
        conn = _make_connection(plexijoin, "lifecycle-a")
        assert plexijoin.activate_connection(conn["id"]) is True
        # Second call is a no-op (rowcount 0), not an error.
        assert plexijoin.activate_connection(conn["id"]) is False
        assert plexijoin.get_connection(conn["id"])["status"] == "active"

    def test_send_message_requires_active_connection(self, plexijoin):
        import asyncio

        conn = _make_connection(plexijoin, "lifecycle-b")
        result = asyncio.run(
            plexijoin.send_message(conn["id"], "channel_message", {"x": 1})
        )
        assert result["success"] is False
        assert "not active" in result["error"]

    def test_send_message_records_outbound_traffic_on_success(
        self, plexijoin, monkeypatch
    ):
        import asyncio

        conn = _make_connection(plexijoin, "traffic-a")
        plexijoin.activate_connection(conn["id"])

        async def _fake_post_signed(self, remote_url, shared_key, path, payload):
            return {"success": True, "status_code": 200}

        monkeypatch.setattr(type(plexijoin), "_post_signed", _fake_post_signed)

        result = asyncio.run(
            plexijoin.send_message(conn["id"], "channel_message", {"content": "hi"})
        )
        assert result["success"] is True

        rows = plexijoin.get_traffic_data(hours=1)
        assert len(rows) >= 1
        assert any(r["direction"] == "outbound" for r in rows)

        refreshed = plexijoin.get_connection(conn["id"])
        assert refreshed["messages_out"] >= 1

    def test_join_request_payload_shape(self, plexijoin, monkeypatch):
        """send_join_request signs the payload with the shared key."""
        import asyncio

        captured = {}

        async def _fake_post_signed(self, remote_url, shared_key, path, payload):
            captured.update(
                {
                    "url": remote_url,
                    "key": shared_key,
                    "path": path,
                    "payload": payload,
                }
            )
            return {"success": True, "status_code": 200}

        monkeypatch.setattr(type(plexijoin), "_post_signed", _fake_post_signed)

        conn = _make_connection(plexijoin, "joinreq-a")
        result = asyncio.run(plexijoin.send_join_request(conn["id"]))
        assert result["success"] is True
        assert captured["key"] == "sekret"
        assert captured["path"].endswith("/federation/join-request")
        assert captured["payload"]["instance_id"]

    def test_signature_round_trip(self):
        from src.core.plexijoin.manager import _sign_payload

        body = json.dumps({"a": 1}).encode()
        sig = _sign_payload("k", body)
        assert sig != _sign_payload("other", body)
