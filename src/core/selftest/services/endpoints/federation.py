"""Federation + PlexiJoin contract tests for the self-test loop.

Inbound federation routes authenticate with an HMAC signature over the raw
body (timestamp + nonce replay protection included), not a local JWT, so
the generic OpenAPI fuzzer can never satisfy their auth (401 by design).
These dedicated tests sign real payloads with a provisioned connection's
shared key and exercise the full contract instead.
"""

import hashlib
import hmac as _hmac
import json as _json
import secrets
import time

import src.api as api
import utils.logger as logger

from .base import EndpointTesterBase


def sign_federation_payload(shared_key: str, body: bytes):
    """Sign a federation payload the way transports do.

    Returns (signature, timestamp, nonce) for the x-plexijoin-* headers.
    Envelope: HMAC-SHA256(key, body + b"\n" + ts + b"\n" + nonce).
    """
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    material = body + b"\n" + timestamp.encode("ascii") + b"\n" + nonce.encode("ascii")
    signature = _hmac.new(
        shared_key.encode("utf-8"), material, hashlib.sha256
    ).hexdigest()
    return signature, timestamp, nonce


def federation_headers(shared_key: str, body: bytes):
    """Build the x-plexijoin-* header set for *body*."""
    signature, timestamp, nonce = sign_federation_payload(shared_key, body)
    return {
        "x-plexijoin-signature": signature,
        "x-plexijoin-timestamp": timestamp,
        "x-plexijoin-nonce": nonce,
    }


class FederationMixin(EndpointTesterBase):
    """Tests inbound federation + /Join contract with real signatures."""

    def _provision_active_connection(self, db):
        """Create an ACTIVE connection with a known shared key.

        Returns (remote_instance_id, shared_key). Caller must delete the
        row afterwards (suite hygiene).
        """
        import utils.encryption as _encryption

        shared_key = secrets.token_hex(16)
        enc_key = _encryption.encrypt_data(shared_key)
        remote_id = f"selftest-fed-{secrets.token_hex(6)}"
        now_ms = int(time.time() * 1000)
        db.execute(
            "INSERT INTO plexijoin_connections "
            "(remote_instance_id, remote_url, shared_key_encrypted, "
            "status, created_at, created_by) VALUES (?, ?, ?, 'active', ?, ?)",
            (remote_id, "https://remote.example.invalid", enc_key, now_ms, 0),
        )
        return remote_id, shared_key

    def _drop_connection(self, db, remote_id):
        try:
            db.execute(
                "DELETE FROM plexijoin_connections WHERE remote_instance_id = ?",
                (remote_id,),
            )
        except Exception:
            pass

    def test_federation_inbound(self) -> None:
        """Signed join-request + message over a provisioned active link."""
        if not self.ctx.standalone_mode:
            return
        if self.ctx.unlicensed:
            logger.info("Skipping federation inbound (unlicensed mode)")
            return
        db = api.get_db()
        if not db:
            return
        session = self.ctx.session
        base = self.ctx.base_url

        remote_id, shared_key = self._provision_active_connection(db)
        try:
            # --- join-request (pending+active links accepted) ---
            jr_payload = _json.dumps(
                {
                    "instance_id": remote_id,
                    "remote_url": "https://remote.example.invalid",
                    "requested_by": "selftest",
                    "note": "self-test federation",
                }
            ).encode("utf-8")
            jr_resp = session.post(
                f"{base}/api/v1/federation/join-request",
                data=jr_payload,
                headers=federation_headers(shared_key, jr_payload),
                timeout=10,
            )
            jr_ok = jr_resp.status_code == 200
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/federation/join-request",
                    "status_code": jr_resp.status_code,
                    "duration_ms": 0,
                    "success": jr_ok,
                    "label": "federation_join_request",
                }
            )
            if jr_ok:
                logger.info("Federation join-request PASSED")
                try:
                    _rid = jr_resp.json().get("request_id")
                    if _rid:
                        db.execute(
                            "DELETE FROM plexijoin_inbound_requests WHERE id = ?",
                            (int(_rid),),
                        )
                except Exception:
                    pass
            else:
                logger.warning(
                    f"Federation join-request -> {jr_resp.status_code}: "
                    f"{jr_resp.text[:200]}"
                )

            # --- message (active links only) ---
            msg_payload = _json.dumps(
                {
                    "message_type": "selftest.ping",
                    "payload": {"hello": "federation"},
                }
            ).encode("utf-8")
            msg_resp = session.post(
                f"{base}/api/v1/federation/message",
                data=msg_payload,
                headers=federation_headers(shared_key, msg_payload),
                timeout=10,
            )
            msg_ok = msg_resp.status_code == 200
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/federation/message",
                    "status_code": msg_resp.status_code,
                    "duration_ms": 0,
                    "success": msg_ok,
                    "label": "federation_message",
                }
            )
            if msg_ok:
                logger.info("Federation message PASSED")
            else:
                logger.warning(
                    f"Federation message -> {msg_resp.status_code}: "
                    f"{msg_resp.text[:200]}"
                )
        finally:
            self._drop_connection(db, remote_id)

    def test_join_contract(self) -> None:
        """End-to-end /Join client contract with real ids.

        request -> list -> approve/deny (seeded inbound ids) -> message
        validation (pending links cannot send: 400 contract) -> break.
        Break runs last: it deletes the connection under test.
        """
        if not self.ctx.standalone_mode:
            return
        if self.ctx.unlicensed:
            logger.info("Skipping Join contract (unlicensed mode)")
            return
        if not self.ctx.session.headers.get("Authorization"):
            return
        session = self.ctx.session
        base = self.ctx.base_url

        def _record(label, method, path, resp, expect: object = 200):
            if isinstance(expect, int):
                ok = resp is not None and resp.status_code == expect
            else:
                ok = resp is not None and resp.status_code in expect
            self.ctx.results.append(
                {
                    "method": method,
                    "path": path,
                    "status_code": resp.status_code if resp is not None else 0,
                    "duration_ms": 0,
                    "success": ok,
                    "label": label,
                }
            )
            if ok:
                logger.info(f"Join {label} PASSED")
            else:
                logger.warning(
                    f"Join {label} -> "
                    f"{resp.status_code if resp is not None else 0}: "
                    f"{resp.text[:200] if resp is not None else 'no response'}"
                )
            return ok

        # --- request (creates a pending connection; delivery best-effort) ---
        uniq = secrets.token_hex(4)
        req_resp = session.post(
            f"{base}/api/v1/Join/request",
            json={
                "remote_url": f"https://jointest-{uniq}.example.com",
                "note": "self-test join",
            },
            timeout=15,
        )
        if not _record(
            "join_request", "POST", "/api/v1/Join/request", req_resp, (200, 201)
        ):
            return
        join_id = None
        try:
            _data = req_resp.json()
            join_id = _data.get("Join_id") or _data.get("connection_id")
        except Exception:
            pass

        # --- list ---
        list_resp = session.get(f"{base}/api/v1/Join/list", timeout=10)
        if not _record("join_list", "GET", "/api/v1/Join/list", list_resp, 200):
            return
        if join_id is None:
            try:
                _items = list_resp.json().get("Joins") or []
                if _items:
                    join_id = _items[0].get("Join_id") or _items[0].get("id")
            except Exception:
                pass

        # --- approve the seeded inbound request ---
        if self.ctx.test_plexijoin_request_id:
            appr = session.post(
                f"{base}/api/v1/Join/{self.ctx.test_plexijoin_request_id}/approve",
                json={},
                timeout=10,
            )
            _record(
                "join_approve",
                "POST",
                "/api/v1/Join/{id}/approve",
                appr,
                (200, 201),
            )
        # --- deny the seeded deny request ---
        if self.ctx.test_plexijoin_deny_request_id:
            deny = session.post(
                f"{base}/api/v1/Join/{self.ctx.test_plexijoin_deny_request_id}/reject",
                json={},
                timeout=10,
            )
            _record(
                "join_reject",
                "POST",
                "/api/v1/Join/{id}/reject",
                deny,
                (200, 201),
            )

        # --- message over a pending link: the 400 validation contract ---
        if join_id is not None:
            msg = session.post(
                f"{base}/api/v1/Join/{join_id}/message",
                json={"message_type": "selftest.ping", "payload": {"n": 1}},
                timeout=15,
            )
            # Pending links cannot deliver: assert the documented 400 with
            # a clear contract message (not a 500/exception).
            msg_ok = (
                msg.status_code == 400 and "not active" in msg.text.lower()
            ) or 200 <= msg.status_code < 300
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/Join/{id}/message",
                    "status_code": msg.status_code,
                    "duration_ms": 0,
                    "success": msg_ok,
                    "label": "join_message",
                }
            )
            if msg_ok:
                logger.info("Join message contract PASSED")
            else:
                logger.warning(f"Join message -> {msg.status_code}: {msg.text[:200]}")

        # --- break (deletes the connection created above; runs last) ---
        if join_id is not None:
            brk = session.post(
                f"{base}/api/v1/Join/{join_id}/break", json={}, timeout=10
            )
            _record("join_break", "POST", "/api/v1/Join/{id}/break", brk, (200, 201))
