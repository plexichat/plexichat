"""Admin endpoint tester mixin.

Tests migration apply/rollback (legacy + new endpoints), access token
rotate/revoke, and delay-deletion in standalone mode.
"""

import time
import secrets

import utils.logger as logger

from .base import EndpointTesterBase


class AdminMixin(EndpointTesterBase):
    """Tests admin-related API endpoints."""

    # -- Approval ceremony -------------------------------------------------
    # Destructive endpoints (migrations, reindex, emergency override) are
    # approval-gated: they require a *consumed* APPROVED request for their
    # action. The ceremony below performs the real multi-admin flow —
    # request (other admin) -> approve (main) -> approve (third admin via
    # an admin-session login) -> execute — so the gates themselves are
    # exercised instead of bypassed.

    def _third_admin_session(self):
        """Log in as the third (support) admin via the admin realm.

        Returns a fresh session carrying an admin-session token, or None.
        The session is caller-owned (must be closed).
        """
        if not getattr(self.ctx, "test_third_username", None):
            return None
        password = self.ctx._test_password
        if not password:
            return None
        try:
            sess = self.ctx.make_session()
            internal_secret = None
            try:
                import src.api as _api

                internal_secret = _api.get_internal_secret()
            except Exception:
                pass
            if internal_secret:
                sess.headers.update({"X-Plexichat-Internal-Secret": internal_secret})
            resp = sess.post(
                f"{self.ctx.base_url}/api/v1/admin/login",
                json={
                    "username": self.ctx.test_third_username,
                    "password": password,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(
                    f"Third admin login -> {resp.status_code}: {resp.text[:200]}"
                )
                try:
                    sess.close()
                except Exception:
                    pass
                return None
            token = resp.json().get("token")
            if not token:
                logger.warning("Third admin login response missing token")
                try:
                    sess.close()
                except Exception:
                    pass
                return None
            sess.headers.update({"Authorization": f"Bearer {token}"})
            return sess
        except Exception as e:
            logger.warning(f"Third admin login exception: {e}")
            return None

    def _approval_ceremony(self, action_type: str):
        """Run the full approval ceremony for *action_type*.

        Returns True when an APPROVED (consumable) request for the action
        exists afterwards — i.e. request + 2 distinct approvals succeeded.
        Every step is logged; failures return False (caller records them).
        """
        base = self.ctx.base_url
        main = self.ctx.session
        other = self.ctx.other_session
        if not main.headers.get("Authorization") or not (
            other and other.headers.get("Authorization")
        ):
            logger.warning(f"Approval ceremony skipped ({action_type}): no sessions")
            return False
        # 1. Request (other admin).
        try:
            req_resp = other.post(
                f"{base}/api/v1/admin/approvals/request",
                json={
                    "action_type": action_type,
                    "target_type": "system",
                    "action_details": f"self-test ceremony for {action_type}",
                },
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Approval request exception ({action_type}): {e}")
            return False
        if req_resp.status_code not in (200, 201):
            logger.warning(
                f"Approval request ({action_type}) -> {req_resp.status_code}: "
                f"{req_resp.text[:200]}"
            )
            return False
        # Resolve the fresh request id (the route returns it inside message).
        approval_id = None
        try:
            import re as _re

            m = _re.search(r"(\d{10,})", req_resp.text)
            if m:
                approval_id = int(m.group(1))
        except Exception:
            pass
        if approval_id is None:
            try:
                import src.api as _api

                _db = _api.get_db()
                if _db:
                    _row = _db.fetch_one(
                        "SELECT id FROM admin_approvals WHERE action_type = ? "
                        "AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                        (action_type,),
                    )
                    if _row:
                        approval_id = int(
                            _row["id"] if isinstance(_row, dict) else _row[0]
                        )
            except Exception:
                pass
        if approval_id is None:
            logger.warning(f"Approval ceremony ({action_type}): no id resolved")
            return False
        # 2. Approve as main.
        try:
            a1 = main.post(
                f"{base}/api/v1/admin/approvals/{approval_id}/approve", timeout=10
            )
        except Exception as e:
            logger.warning(f"Approval approve#1 exception ({action_type}): {e}")
            return False
        if a1.status_code not in (200, 201):
            logger.warning(
                f"Approval approve#1 ({action_type}) -> {a1.status_code}: "
                f"{a1.text[:200]}"
            )
            return False
        # 3. Approve as third admin (second distinct vote -> approved).
        third_sess = self._third_admin_session()
        if third_sess is None:
            return False
        try:
            a2 = third_sess.post(
                f"{base}/api/v1/admin/approvals/{approval_id}/approve", timeout=10
            )
        except Exception as e:
            logger.warning(f"Approval approve#2 exception ({action_type}): {e}")
            return False
        finally:
            try:
                third_sess.close()
            except Exception:
                pass
        if a2.status_code not in (200, 201):
            logger.warning(
                f"Approval approve#2 ({action_type}) -> {a2.status_code}: "
                f"{a2.text[:200]}"
            )
            return False
        logger.info(f"Approval ceremony complete for {action_type} (id={approval_id})")
        return True

    def test_migration_endpoints(self) -> None:
        """Test migration apply and rollback using the 001 selftest no-op migration.

        Approval-gated: each execution is preceded by the real multi-admin
        ceremony (request + 2 approvals), so the gates are exercised.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping migration tests (no auth token)")
            return

        session = self.ctx.session
        mig_path_apply = "/api/v1/admin/database/migrations/apply/001"
        mig_path_rollback = "/api/v1/admin/database/migrations/rollback/001"

        # Migration 001 may already be applied during server startup.
        # Rollback first so we can test apply; then rollback again to leave
        # the DB in the same state we found it.
        logger.info(
            "Testing POST /api/v1/admin/database/migrations/rollback/001 (pre-test)..."
        )
        if not self._approval_ceremony("migrations.rollback"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_path_rollback,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "migration_rollback_pre",
                    "error": "approval ceremony failed",
                }
            )
            return
        rb1_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_rollback}",
            timeout=10,
        )
        rb1_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_rollback,
                "status_code": resp.status_code,
                "duration_ms": (time.time() - rb1_start) * 1000,
                "success": rb1_ok,
                "label": "migration_rollback_pre",
            }
        )
        if rb1_ok:
            logger.info(
                f"Migration rollback 001 (pre-test) PASSED -> {resp.status_code}"
            )
        else:
            logger.debug(
                f"Migration rollback 001 (pre-test) -> {resp.status_code}: {resp.text[:200]}"
            )

        # --- Apply migration 001 ---
        logger.info("Testing POST /api/v1/admin/database/migrations/apply/001...")
        if not self._approval_ceremony("migrations.run"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_path_apply,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "migration_apply",
                    "error": "approval ceremony failed",
                }
            )
            return
        mig_apply_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_apply}",
            timeout=10,
        )
        duration = (time.time() - mig_apply_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_apply,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "migration_apply",
            }
        )
        if success:
            logger.info(f"Migration apply 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration apply 001 -> {resp.status_code}: {resp.text[:200]}"
            )

        # --- Rollback migration 001 ---
        logger.info("Testing POST /api/v1/admin/database/migrations/rollback/001...")
        if not self._approval_ceremony("migrations.rollback"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_path_rollback,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "migration_rollback",
                    "error": "approval ceremony failed",
                }
            )
            return
        mig_rollback_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_path_rollback}",
            timeout=10,
        )
        duration = (time.time() - mig_rollback_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_path_rollback,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "migration_rollback",
            }
        )
        if success:
            logger.info(f"Migration rollback 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration rollback 001 -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_access_token_rotate(self) -> None:
        """Test the full access-token lifecycle: rotate -> revoke -> unrevoke.

        Rotation values are always generated server-side (caller-supplied
        values are rejected with 400 by design), so the rotate posts an
        empty body. Revoke/unrevoke are stateful (404 unless the state
        matches), hence the strict lifecycle order here — the generic
        auto-loop cannot satisfy it, so these three endpoints are covered
        here instead of there.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_access_token_id:
            logger.debug("Skipping access token rotate (no token_id)")
            return

        session = self.ctx.session
        base = self.ctx.base_url
        token_id = self.ctx.test_access_token_id

        def _record(label, method, path, resp):
            ok = resp is not None and 200 <= resp.status_code < 300
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
                logger.info(f"Access token {label} PASSED")
            else:
                logger.warning(
                    f"Access token {label} -> "
                    f"{resp.status_code if resp is not None else 0}: "
                    f"{resp.text[:200] if resp is not None else 'no response'}"
                )
            return ok

        # --- Rotate (empty body: value generated server-side) ---
        logger.info(
            f"Testing POST /api/v1/admin/security/access-tokens/{token_id}/rotate..."
        )
        resp = session.post(
            f"{base}/api/v1/admin/security/access-tokens/{token_id}/rotate",
            json={},
            timeout=5,
        )
        if not _record(
            "rotate",
            "POST",
            f"/api/v1/admin/security/access-tokens/{token_id}/rotate",
            resp,
        ):
            return
        rotated_token_id = token_id
        try:
            rotate_data = resp.json()
            new_id = (
                rotate_data.get("access_token", {}).get("id")
                or rotate_data.get("id")
                or rotate_data.get("token_id")
            )
            if new_id:
                rotated_token_id = int(new_id)
                logger.debug(f"Rotate returned new token id: {rotated_token_id}")
        except Exception:
            pass
        # The rotated-away original is revoked by rotation; point tracking
        # at the live replacement so cleanup deletes the right row.
        self.ctx.test_access_token_id = rotated_token_id

        # --- Revoke the replacement ---
        logger.info(
            f"Testing POST /api/v1/admin/security/access-tokens/{rotated_token_id}/revoke..."
        )
        resp = session.post(
            f"{base}/api/v1/admin/security/access-tokens/{rotated_token_id}/revoke",
            timeout=5,
        )
        if not _record(
            "revoke",
            "POST",
            f"/api/v1/admin/security/access-tokens/{rotated_token_id}/revoke",
            resp,
        ):
            return

        # --- Unrevoke (valid only because the token is now revoked) ---
        logger.info(
            f"Testing POST /api/v1/admin/security/access-tokens/{rotated_token_id}/unrevoke..."
        )
        resp = session.post(
            f"{base}/api/v1/admin/security/access-tokens/{rotated_token_id}/unrevoke",
            timeout=5,
        )
        if not _record(
            "unrevoke",
            "POST",
            f"/api/v1/admin/security/access-tokens/{rotated_token_id}/unrevoke",
            resp,
        ):
            return

        # --- Revoke again so no live credential survives the suite ---
        logger.info(f"Re-revoking access token {rotated_token_id} (suite hygiene)...")
        resp = session.post(
            f"{base}/api/v1/admin/security/access-tokens/{rotated_token_id}/revoke",
            timeout=5,
        )
        if resp is not None and 200 <= resp.status_code < 300:
            logger.info("Re-revoke PASSED (not separately scored)")

    def test_delay_deletion(self) -> None:
        """Test delay-deletion using the API-created other_user."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_other_user_id:
            logger.debug("Skipping delay-deletion (no other_user_id)")
            return

        session = self.ctx.session
        other_id = self.ctx.test_other_user_id

        # Delete the user via API first
        logger.info("Testing DELETE /api/v1/users/@me (other_user)...")
        other_session = self.ctx.other_session
        if other_session and other_session.headers.get("Authorization"):
            resp = other_session.delete(
                f"{self.ctx.base_url}/api/v1/users/@me",
                json={"password": self.ctx._test_password},
                timeout=30,
            )
            success = resp.status_code in (200, 204)
            self.ctx.results.append(
                {
                    "method": "DELETE",
                    "path": "/api/v1/users/@me",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": success,
                    "label": "other_user_self_delete",
                }
            )
        # --- Delay deletion ---
        future_deletion_at = int(time.time()) + 86400
        logger.info(f"Testing POST /api/v1/admin/users/{other_id}/delay-deletion...")
        delay_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/admin/users/{other_id}/delay-deletion",
            params={"deletion_at": str(future_deletion_at)},
            timeout=5,
        )
        duration = (time.time() - delay_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/admin/users/{other_id}/delay-deletion",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "delay_deletion",
            }
        )
        if success:
            logger.info(f"Delay deletion PASSED -> {resp.status_code}")
        else:
            logger.warning(f"Delay deletion -> {resp.status_code}: {resp.text[:200]}")

    def test_transcript_export(self) -> None:
        """Test the full transcript export workflow for a server channel.

        Steps (chaining the created export_id from the POST into the GETs):
            1. Ensure the test channel has a message (send one if needed).
            2. POST a transcript export request -> capture export_id.
            3. GET the export status using the real export_id.
            4. GET the export download using the real export_id.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping transcript export tests (no auth token)")
            return
        if not self.ctx.test_channel_id or not self.ctx.test_server_id:
            logger.debug("Skipping transcript export tests (no test channel)")
            return

        session = self.ctx.session
        channel_id = self.ctx.test_channel_id

        # Step 1: Guarantee the channel has at least one message to export.
        msg_start = time.time()
        msg_resp = session.post(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages",
            json={"content": f"self-test export message {secrets.token_hex(4)}"},
            timeout=10,
        )
        msg_duration = (time.time() - msg_start) * 1000
        msg_ok = 200 <= msg_resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/channels/{channel_id}/messages",
                "status_code": msg_resp.status_code,
                "duration_ms": msg_duration,
                "success": msg_ok,
                "label": "transcript_export_seed_message",
            }
        )
        if not msg_ok:
            logger.warning(
                f"Transcript export seed message -> {msg_resp.status_code}: "
                f"{msg_resp.text[:200]}"
            )
            return

        # Step 2: Request the transcript export.
        logger.info(
            f"Testing POST /api/v1/channels/{channel_id}/messages/export (request)..."
        )
        req_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/export",
            json={"format": "json"},
            timeout=30,
        )
        req_duration = (time.time() - req_start) * 1000
        req_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/channels/{channel_id}/messages/export",
                "status_code": resp.status_code,
                "duration_ms": req_duration,
                "success": req_ok,
                "label": "transcript_export_request",
            }
        )
        if not req_ok:
            logger.warning(
                f"Transcript export request -> {resp.status_code}: {resp.text[:200]}"
            )
            return

        export_id = None
        try:
            data = resp.json()
            export_id = data.get("export_id")
            if export_id:
                self.ctx.test_export_id = str(export_id)
                logger.debug(f"Captured transcript export id: {export_id}")
        except Exception:
            pass

        if not export_id:
            logger.warning("Transcript export response missing export_id")
            return

        # Step 3: Fetch the export status using the real export_id.
        logger.info(
            f"Testing GET /api/v1/channels/{channel_id}/messages/export/"
            f"{export_id} (status)..."
        )
        status_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/export/"
            f"{export_id}",
            timeout=10,
        )
        status_duration = (time.time() - status_start) * 1000
        status_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": f"/api/v1/channels/{channel_id}/messages/export/{export_id}",
                "status_code": resp.status_code,
                "duration_ms": status_duration,
                "success": status_ok,
                "label": "transcript_export_status",
            }
        )
        if status_ok:
            logger.info(f"Transcript export status PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Transcript export status -> {resp.status_code}: {resp.text[:200]}"
            )

        # Step 4: Download the export using the real export_id.
        logger.info(
            f"Testing GET /api/v1/channels/{channel_id}/messages/export/"
            f"{export_id}/download (download)..."
        )
        dl_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/export/"
            f"{export_id}/download",
            timeout=30,
        )
        dl_duration = (time.time() - dl_start) * 1000
        dl_ok = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": (
                    f"/api/v1/channels/{channel_id}/messages/export/"
                    f"{export_id}/download"
                ),
                "status_code": resp.status_code,
                "duration_ms": dl_duration,
                "success": dl_ok,
                "label": "transcript_export_download",
            }
        )
        if dl_ok:
            logger.info(f"Transcript export download PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Transcript export download -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_dsar(self) -> None:
        """Test the full user-facing data export (DSAR) workflow.

        Mirrors the patterns used by `test_delay_deletion` but for the
        right-to-portability flow. We avoid admin-only endpoints because
        the user is the one driving the request, and we want to make sure
        the self-test exercises the same path real users hit.

        Steps:
            1. User requests a data export.
            2. User lists their data export requests.
            3. User fetches a single request status.
            4. User cancels the request (cleanup).
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping DSAR tests (no auth token)")
            return

        session = self.ctx.session
        test_pass = self.ctx._test_password
        assert test_pass, "self-test password must be set up before requests"

        dsar_request_id = None

        # Step 1: User requests a data export.
        logger.info(
            "Testing POST /api/v1/users/@me/data-export (request data export)..."
        )
        request_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            json={"password": test_pass, "format": "json"},
            timeout=10,
        )
        duration = (time.time() - request_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/users/@me/data-export",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_request",
            }
        )
        if success:
            logger.info(f"DSAR request PASSED -> {resp.status_code}")
        else:
            logger.warning(f"DSAR request -> {resp.status_code}: {resp.text[:200]}")
            return  # nothing else to test if the request itself failed

        # Step 2: User lists their data export requests.
        logger.info("Testing GET /api/v1/users/@me/data-export (list)...")
        list_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            timeout=10,
        )
        duration = (time.time() - list_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": "/api/v1/users/@me/data-export",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_list",
            }
        )
        if success:
            try:
                data = resp.json()
                items = data.get("requests", [])
                if items:
                    dsar_request_id = items[0].get("id")
                    if dsar_request_id:
                        self.ctx.test_dsar_id = int(dsar_request_id)
                        logger.debug(f"Found DSAR request id: {dsar_request_id}")
            except Exception:
                pass
            logger.info(f"DSAR list PASSED -> {resp.status_code}")
        else:
            logger.warning(f"DSAR list -> {resp.status_code}: {resp.text[:200]}")

        if not dsar_request_id and self.ctx.test_dsar_id:
            dsar_request_id = self.ctx.test_dsar_id

        if not dsar_request_id:
            return

        # Step 3: User fetches a single request status.
        logger.info(
            f"Testing GET /api/v1/users/@me/data-export/{dsar_request_id} (status)..."
        )
        status_start = time.time()
        resp = session.get(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export/{dsar_request_id}",
            timeout=10,
        )
        duration = (time.time() - status_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "GET",
                "path": f"/api/v1/users/@me/data-export/{dsar_request_id}",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_status",
            }
        )
        if success:
            logger.info(f"DSAR status PASSED -> {resp.status_code}")
        else:
            logger.warning(f"DSAR status -> {resp.status_code}: {resp.text[:200]}")

        # Steps 3b-3d: admin lifecycle on the same request (detail ->
        # approve -> generate). Runs before the user cancels below.
        base = self.ctx.base_url
        admin_steps = [
            (
                "GET",
                f"/api/v1/admin/data-export/{dsar_request_id}",
                "dsar_admin_get",
                None,
            ),
            (
                "POST",
                f"/api/v1/admin/data-export/{dsar_request_id}/approve",
                "dsar_admin_approve",
                None,
            ),
            (
                "POST",
                f"/api/v1/admin/data-export/{dsar_request_id}/generate",
                "dsar_admin_generate",
                None,
            ),
        ]
        for method, path, label, body in admin_steps:
            try:
                if method == "GET":
                    r = session.get(f"{base}{path}", timeout=15)
                else:
                    r = session.post(f"{base}{path}", json=body or {}, timeout=60)
                ok = 200 <= r.status_code < 300
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": r.status_code,
                        "duration_ms": 0,
                        "success": ok,
                        "label": label,
                    }
                )
                if ok:
                    logger.info(f"DSAR {label} PASSED -> {r.status_code}")
                else:
                    logger.warning(f"DSAR {label} -> {r.status_code}: {r.text[:200]}")
            except Exception as e:
                logger.warning(f"DSAR {label} exception: {e}")
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": label,
                        "error": str(e)[:200],
                    }
                )

        # Step 4: User cancels the request (cleanup).

        # Step 4: User cancels the request (cleanup).
        logger.info(
            f"Testing DELETE /api/v1/users/@me/data-export/{dsar_request_id} (cancel)..."
        )
        cancel_start = time.time()
        resp = session.delete(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export/{dsar_request_id}",
            timeout=10,
        )
        duration = (time.time() - cancel_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "DELETE",
                "path": f"/api/v1/users/@me/data-export/{dsar_request_id}",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "dsar_cancel",
            }
        )
        if success:
            logger.info(f"DSAR cancel PASSED -> {resp.status_code}")
            self.ctx.test_dsar_id = None
        else:
            logger.warning(f"DSAR cancel -> {resp.status_code}: {resp.text[:200]}")

        # Step 5: deny flow on a second request (deny requires pending, so
        # it needs its own row). The denied row is terminal by design.
        logger.info("Testing DSAR deny flow (second request)...")
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            json={"password": test_pass, "format": "json"},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            deny_id = None
            try:
                import src.api as _api2

                _db2 = _api2.get_db()
                if _db2:
                    _row2 = _db2.fetch_one(
                        "SELECT id FROM dsar_requests WHERE user_id = ? "
                        "AND status = 'pending' ORDER BY requested_at DESC LIMIT 1",
                        (self.ctx.test_user_id,),
                    )
                    if _row2:
                        deny_id = int(
                            _row2["id"] if isinstance(_row2, dict) else _row2[0]
                        )
            except Exception:
                pass
            if deny_id:
                deny_resp = session.post(
                    f"{self.ctx.base_url}/api/v1/admin/data-export/{deny_id}/deny",
                    json={"reason": "self-test deny flow"},
                    timeout=15,
                )
                deny_ok = 200 <= deny_resp.status_code < 300
                self.ctx.results.append(
                    {
                        "method": "POST",
                        "path": f"/api/v1/admin/data-export/{deny_id}/deny",
                        "status_code": deny_resp.status_code,
                        "duration_ms": 0,
                        "success": deny_ok,
                        "label": "dsar_admin_deny",
                    }
                )
                if deny_ok:
                    logger.info("DSAR admin deny PASSED")
                else:
                    logger.warning(
                        f"DSAR admin deny -> {deny_resp.status_code}: "
                        f"{deny_resp.text[:200]}"
                    )

    def test_new_migration_endpoints(self) -> None:
        """Test the new migration run/rollback endpoints (admin/migrations.py).

        Unlike the legacy /admin/database/migrations/* endpoints tested in
        test_migration_endpoints(), these use the richer MigrationRunRequest
        body with dry_run support and irreversible-migration checks. The run
        uses dry_run (no live-DB mutation); rollback targets the 001 no-op.
        Both are approval-gated: the real multi-admin ceremony precedes each.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping new migration tests (no auth token)")
            return

        session = self.ctx.session
        mig_run = "/api/v1/admin/migrations/001/run"
        mig_rollback = "/api/v1/admin/migrations/001/rollback"

        # --- Run (apply) migration 001 via new endpoint (dry-run) ---
        logger.info("Testing POST /api/v1/admin/migrations/001/run (dry-run)...")
        if not self._approval_ceremony("migrations.run"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_run,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "new_migration_apply",
                    "error": "approval ceremony failed",
                }
            )
            return
        run_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_run}",
            json={"dry_run": True},
            timeout=10,
        )
        duration = (time.time() - run_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_run,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "new_migration_apply",
            }
        )
        if success:
            logger.info(f"Migration apply 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration apply 001 -> {resp.status_code}: {resp.text[:200]}"
            )

        # --- Rollback 001 via new endpoint ---
        logger.info("Testing POST /api/v1/admin/migrations/001/rollback...")
        if not self._approval_ceremony("migrations.rollback"):
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": mig_rollback,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "label": "new_migration_rollback",
                    "error": "approval ceremony failed",
                }
            )
            return
        rb_start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}{mig_rollback}",
            timeout=10,
        )
        duration = (time.time() - rb_start) * 1000
        success = 200 <= resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": mig_rollback,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "new_migration_rollback",
            }
        )
        if success:
            logger.info(f"Migration rollback 001 PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Migration rollback 001 -> {resp.status_code}: {resp.text[:200]}"
            )

    def test_approval_gated_actions(self) -> None:
        """Execute the remaining approval-gated endpoints with real approvals.

        Each gated action consumes a single-use approval, so the ceremony
        (request + 2 approvals) runs per action. Safe variants are used
        throughout: dry-runs for apply-all, short-lived override tokens,
        and the tiny test corpus for reindex.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            return
        session = self.ctx.session
        base = self.ctx.base_url

        cases = [
            # (label, action, method, path, json_body, params)
            (
                "migration_apply_all",
                "migrations.apply_all",
                "POST",
                "/api/v1/admin/database/migrations/apply-all",
                None,
                {"dry_run": "true"},
            ),
            (
                "migration_emergency_override",
                "migrations.emergency_override",
                "POST",
                "/api/v1/admin/migrations/emergency-override",
                {"reason": "self-test ceremony", "expires_minutes": 5},
                None,
            ),
            (
                "search_reindex",
                "search.reindex",
                "POST",
                "/api/v1/admin/search/reindex",
                {},
                None,
            ),
            (
                "search_reindex_standalone",
                "search.reindex",
                "POST",
                "/api/v1/admin/search/reindex/standalone",
                {},
                None,
            ),
        ]
        for label, action, method, path, body, params in cases:
            logger.info(f"Approval-gated test {label} ({method} {path})...")
            if not self._approval_ceremony(action):
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": label,
                        "error": "approval ceremony failed",
                    }
                )
                continue
            start = time.time()
            try:
                resp = session.request(
                    method, f"{base}{path}", json=body, params=params, timeout=60
                )
                duration = (time.time() - start) * 1000
                ok = 200 <= resp.status_code < 300
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": resp.status_code,
                        "duration_ms": duration,
                        "success": ok,
                        "label": label,
                    }
                )
                if ok:
                    logger.info(f"Approval-gated {label} PASSED -> {resp.status_code}")
                else:
                    logger.warning(
                        f"Approval-gated {label} -> {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
            except Exception as e:
                logger.warning(f"Approval-gated {label} exception: {e}")
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": path,
                        "status_code": 0,
                        "duration_ms": 0,
                        "success": False,
                        "label": label,
                        "error": str(e)[:200],
                    }
                )
