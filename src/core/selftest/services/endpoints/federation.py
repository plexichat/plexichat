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
        """End-to-end /Join client contract with real ids and real delivery.

        A fake remote PlexiChat instance (local HTTPS server) runs alongside
        the suite, so every delivery step genuinely executes: the manager
        SSRF-validates, HMAC-signs and POSTs, and the fake peer answers with
        valid data.

        request -> list -> approve/deny (fresh inbound ids) -> message
        (delivered to the fake peer) -> break. Break runs last: it deletes
        the connection under test.
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

        # --- fake remote peer (local plain-HTTP server, valid answers) ---
        from src.core.selftest.services.fake_remote import FakeRemoteInstance

        fake = FakeRemoteInstance()
        try:
            fake.start()
        except Exception as _e:
            logger.warning(f"Join fake remote failed to start: {_e}")
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/Join/request",
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "join_request",
                }
            )
            return

        try:
            self._join_contract_steps(session, base, fake, _record)
        finally:
            try:
                fake.stop()
            except Exception:
                pass

    def _join_contract_steps(self, session, base, fake, _record) -> None:
        """request -> list -> approve/deny -> message -> break via fake peer."""
        # --- request (creates the connection; delivery to the fake peer
        # is real, so assert the server confirms delivery) ---
        req_resp = session.post(
            f"{base}/api/v1/Join/request",
            json={
                "remote_url": fake.base_url,
                "note": "self-test join",
            },
            timeout=15,
        )
        try:
            _parsed = req_resp.json() if req_resp is not None else None
        except Exception:
            _parsed = None
        _req_json = _parsed if isinstance(_parsed, dict) else {}
        _delivered = bool(_req_json.get("delivered"))
        # A reused connection from an earlier run reports existing=true
        # without redelivering; delivery is still proven live by the
        # message step below, so only fresh creates must show delivered.
        _existing = bool(_req_json.get("existing"))
        if not (_delivered or _existing):
            logger.warning(
                "Join request delivered=false "
                f"(status={req_resp.status_code if req_resp is not None else 0})"
            )
        _req_ok = req_resp is not None and req_resp.status_code in (
            200,
            201,
        )
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/Join/request",
                "status_code": req_resp.status_code if req_resp is not None else 0,
                "duration_ms": 0,
                "success": bool(_req_ok and (_delivered or _existing)),
                "label": "join_request",
            }
        )
        if not (_req_ok and (_delivered or _existing)):
            return
        join_id = None
        if isinstance(_req_json, dict):
            join_id = _req_json.get("Join_id") or _req_json.get("connection_id")

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

        # --- inbound rows, seeded fresh here ---
        # The setup-seeded rows are consumed by the admin plexijoin review
        # endpoints in the auto-loop, so this test mints its own pending
        # rows. The approve row shares the new connection's
        # remote_instance_id, which makes approval ACTIVATE the link (the
        # precondition for delivering a real message below).
        import src.api as _api2

        _remote_id = ""
        # A reused connection from an earlier run still points at that
        # run's (now dead) fake peer: repoint it at the live one and reset
        # to pending so approve reactivates it deterministically.
        if _existing and join_id is not None:
            try:
                _db0 = _api2.get_db()
                if _db0:
                    from urllib.parse import urlparse as _up

                    _new_netloc = _up(fake.base_url).netloc
                    _db0.execute(
                        "UPDATE plexijoin_connections SET remote_url = ?, remote_instance_id = ?, status = 'pending' WHERE id = ?",
                        (
                            fake.base_url,
                            f"remote:{_new_netloc}",
                            int(join_id),
                        ),
                    )
            except Exception as _e:
                logger.debug(f"Join repoint note: {_e}")
        try:
            _db2 = _api2.get_db()
            if _db2 and join_id is not None:
                _conn_row = _db2.fetch_one(
                    "SELECT remote_instance_id FROM plexijoin_connections WHERE id = ?",
                    (int(join_id),),
                )
                if _conn_row:
                    _remote_id = str(
                        _conn_row["remote_instance_id"]
                        if isinstance(_conn_row, dict)
                        else _conn_row[0]
                    )
        except Exception as _e:
            logger.debug(f"Join remote-id lookup note: {_e}")
        _fresh_approve = None
        _fresh_deny = None
        try:
            _db2 = _api2.get_db()
            if _db2:
                import time as _t

                _fresh_approve = self.ctx.data.generate_snowflake()
                _db2.execute(
                    "INSERT INTO plexijoin_inbound_requests (id, remote_instance_id, remote_url, status, requested_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        _fresh_approve,
                        _remote_id,
                        fake.base_url,
                        "pending",
                        int(_t.time() * 1000),
                    ),
                )
                _fresh_deny = self.ctx.data.generate_snowflake()
                _db2.execute(
                    "INSERT INTO plexijoin_inbound_requests (id, remote_instance_id, remote_url, status, requested_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        _fresh_deny,
                        f"test-join-deny-{secrets.token_hex(4)}",
                        "https://remote.example.invalid",
                        "pending",
                        int(_t.time() * 1000),
                    ),
                )
        except Exception as _e:
            logger.debug(f"Join fresh-row seeding note: {_e}")
        # --- approve the fresh inbound request (also activates the link) ---
        if _fresh_approve:
            appr = session.post(
                f"{base}/api/v1/Join/{_fresh_approve}/approve",
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
        # --- deny the fresh deny request ---
        if _fresh_deny:
            deny = session.post(
                f"{base}/api/v1/Join/{_fresh_deny}/reject",
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

        # --- message over the now-ACTIVE link, really delivered ---
        # The manager signs the envelope and POSTs it to the fake peer;
        # success requires BOTH the 2xx here AND the fake peer's record of
        # a signed envelope carrying our payload.
        if join_id is not None:
            msg = session.post(
                f"{base}/api/v1/Join/{join_id}/message",
                json={"message_type": "selftest.ping", "payload": {"n": 1}},
                timeout=15,
            )
            from src.core.plexijoin.manager import (
                FEDERATION_MESSAGE_PATH as _MSG_PATH,
            )

            _seen = fake.received_for(_MSG_PATH)
            _signed_ok = any(
                "x-plexijoin-signature" in r["headers"]
                and "x-plexijoin-timestamp" in r["headers"]
                and isinstance(r["json"], dict)
                and r["json"].get("message_type") == "selftest.ping"
                and isinstance(r["json"].get("payload"), dict)
                and r["json"]["payload"].get("n") == 1
                for r in _seen
            )
            msg_ok = msg is not None and 200 <= msg.status_code < 300 and _signed_ok
            if not _signed_ok:
                logger.warning(
                    "Join message reached no signed envelope at fake peer "
                    f"(status={msg.status_code if msg is not None else 0}, "
                    f"seen={len(_seen)})"
                )
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/Join/{id}/message",
                    "status_code": msg.status_code if msg is not None else 0,
                    "duration_ms": 0,
                    "success": msg_ok,
                    "label": "join_message",
                }
            )
            if msg_ok:
                logger.info("Join message PASSED (delivered to fake peer)")
            else:
                logger.warning(
                    f"Join message -> "
                    f"{msg.status_code if msg is not None else 0}: "
                    f"{msg.text[:200] if msg is not None else 'no response'}"
                )

        # --- break (deletes the connection created above; runs last) ---
        if join_id is not None:
            brk = session.post(
                f"{base}/api/v1/Join/{join_id}/break", json={}, timeout=10
            )
            _record("join_break", "POST", "/api/v1/Join/{id}/break", brk, (200, 201))

        # --- hygiene: remove this run's inbound rows (break deletes the
        # connection; the approved/denied rows would otherwise linger) ---
        try:
            _db3 = _api2.get_db()
            if _db3:
                for _rid in (_fresh_approve, _fresh_deny):
                    if _rid is not None:
                        _db3.execute(
                            "DELETE FROM plexijoin_inbound_requests WHERE id = ?",
                            (_rid,),
                        )
        except Exception as _e:
            logger.debug(f"Join inbound cleanup note: {_e}")
