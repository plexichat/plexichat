"""Admin endpoint tester mixin.

Tests migration apply/rollback (legacy + new endpoints), access token
rotate/revoke, and delay-deletion in standalone mode.
"""

import json
import time
import secrets
from typing import Any, Dict

import src.api as api
import utils.logger as logger

from .base import EndpointTesterBase


def _response_object(resp: Any) -> Dict[str, Any]:
    """The JSON object a response carries, or an empty dict."""
    try:
        data = resp.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _row_value(row: Any, column: str = "id") -> Any:
    """One column of a fetched row, whichever shape the driver returns."""
    if row is None:
        return None
    if isinstance(row, dict):
        if column in row:
            return row[column]
        return next(iter(row.values()), None)
    if isinstance(row, (list, tuple)):
        return row[0] if row else None
    return row


class AdminMixin(EndpointTesterBase):
    """Tests admin-related API endpoints."""

    # -- Approval ceremony -------------------------------------------------
    # Destructive endpoints (migrations, reindex, emergency override) are
    # approval-gated: they require a *consumed* APPROVED request for their
    # action. The ceremony below performs the real multi-admin flow —
    # request (other admin) -> approve (main) -> approve (third admin via
    # an admin-session login) -> execute — so the gates themselves are
    # exercised instead of bypassed.

    def _third_user_session(self):
        from src.core.selftest.services.approval_ceremony import (
            third_user_session as _shared_third,
        )

        return _shared_third(self.ctx)

    def _ensure_user_session(self, session, username: str) -> bool:
        from src.core.selftest.services.approval_ceremony import (
            ensure_user_session as _shared_ensure,
        )

        return bool(_shared_ensure(self.ctx, session, username))

    def _approval_ceremony(self, action_type: str):
        from src.core.selftest.services.approval_ceremony import (
            run_ceremony as _shared_ceremony,
        )

        return bool(_shared_ceremony(self.ctx, action_type))

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

    # -- Purge residue -----------------------------------------------------
    # Opt-in: ``main.py self-test --purge-residue``. The check ends by purging
    # an account, so it is never part of the default suite.

    def _purge_residue_result(
        self,
        label: str,
        method: str,
        path: str,
        success: bool,
        status_code: int = 0,
        duration_ms: float = 0.0,
        error: str = "",
    ) -> None:
        """Record one purge-residue assertion, with detail when it failed."""
        entry: Dict[str, Any] = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "success": success,
            "label": label,
        }
        if error:
            entry["error"] = error
        self.ctx.results.append(entry)

    def test_purge_residue(self) -> None:
        """Erase a throwaway account and prove it left nothing behind.

        Opt-in (``self-test --purge-residue``) because it deletes an account.
        The account is given one of everything a purge is responsible for --
        a server, a channel, a message, an upload and a DM with the test
        admin -- then erased through the real admin force-purge endpoint. The
        database is read back afterwards: deleting only the ``auth_users``
        row used to leave the server, channel, conversation and upload behind,
        owned by a user id that no longer existed. That residue is what this
        check fails on, instead of letting the next run's pre-test sweep
        quietly remove the evidence.
        """
        if not self.ctx.purge_residue:
            logger.debug("Skipping purge-residue check (not enabled)")
            return
        if not self.ctx.standalone_mode:
            logger.debug("Skipping purge-residue check (not in standalone mode)")
            return

        base = self.ctx.base_url
        admin_session = self.ctx.session
        password = self.ctx._test_password
        admin_id = self.ctx.test_user_id
        db = api.get_db()
        auth_mod = api.get_auth()
        if (
            not admin_session.headers.get("Authorization")
            or not password
            or not admin_id
            or db is None
            or auth_mod is None
        ):
            self._purge_residue_result(
                "purge_residue_setup",
                "POST",
                "/api/v1/admin/users/{user_id}/force-purge",
                False,
                error="admin session, test user, database or auth module unavailable",
            )
            return

        tag = secrets.token_hex(4)
        username = f"selftest_purge_{tag}"
        purge_path = "/api/v1/admin/users/{user_id}/force-purge"
        doomed = self.ctx.make_session()
        user_id = None
        server_id = None
        channel_id = None
        channel_conversation_id = None
        dm_id = None

        try:
            # 1. A throwaway account with its own footprint. Registration goes
            #    through the in-process path the rest of setup uses, so the
            #    reserved selftest prefix and the per-IP throttle do not get in
            #    the way.
            user = auth_mod.register_selftest(
                username, f"{username}@selftest.plexichat.com", password
            )
            user_id = int(user.id)

            seed_start = time.time()
            login_resp = doomed.post(
                f"{base}/api/v1/auth/login",
                json={"username": username, "password": password},
                headers={"X-Plexichat-Token-Mode": "bearer"},
                timeout=10,
            )
            login_data = _response_object(login_resp)
            token = login_data.get("token") or login_data.get("access_token")
            if not token:
                self._purge_residue_result(
                    "purge_residue_seed",
                    "POST",
                    "/api/v1/auth/login",
                    False,
                    login_resp.status_code,
                    (time.time() - seed_start) * 1000,
                    f"no token in login response: {login_resp.text[:120]}",
                )
                return
            doomed.headers.update({"Authorization": f"Bearer {token}"})

            server_resp = doomed.post(
                f"{base}/api/v1/servers/",
                json={"name": f"selftest residue {tag}"},
                timeout=10,
            )
            server_id = _row_value(_response_object(server_resp))
            server_id = int(server_id) if server_id else None

            if server_id:
                channel_resp = doomed.post(
                    f"{base}/api/v1/servers/{server_id}/channels",
                    json={"name": "residue-chat", "channel_type": "text"},
                    timeout=10,
                )
                channel_id = _row_value(_response_object(channel_resp))
                channel_id = int(channel_id) if channel_id else None

            upload_ok = False
            if channel_id:
                doomed.post(
                    f"{base}/api/v1/channels/{channel_id}/messages",
                    json={"content": "purge-residue check"},
                    timeout=10,
                )
                upload_resp = doomed.post(
                    f"{base}/api/v1/channels/{channel_id}/attachments",
                    files={
                        "file": (
                            f"residue-{tag}.txt",
                            f"purge residue {tag}".encode(),
                            "text/plain",
                        )
                    },
                    timeout=30,
                )
                upload_ok = 200 <= upload_resp.status_code < 300

            dm_resp = doomed.post(
                f"{base}/api/v1/users/@me/channels",
                json={"recipient_id": str(admin_id)},
                timeout=10,
            )
            dm_id = _row_value(_response_object(dm_resp))
            dm_id = int(dm_id) if dm_id else None

            missing = [
                name
                for name, value in (
                    ("server", server_id),
                    ("channel", channel_id),
                    ("upload", upload_ok),
                    ("dm", dm_id),
                )
                if not value
            ]
            self._purge_residue_result(
                "purge_residue_seed",
                "POST",
                "/api/v1/servers/",
                not missing,
                201,
                (time.time() - seed_start) * 1000,
                f"could not create: {', '.join(missing)}" if missing else "",
            )
            if missing:
                return

            # 2. What the purge is about to owe: the channel's conversation and
            #    every conversation this account participates in.
            channel_conversation_id = _row_value(
                db.fetch_one(
                    "SELECT conversation_id FROM srv_channels WHERE id = ?",
                    (channel_id,),
                ),
                "conversation_id",
            )
            channel_conversation_id = (
                int(channel_conversation_id) if channel_conversation_id else None
            )
            owned_conversations = [
                int(_row_value(row, "conversation_id"))
                for row in (
                    db.fetch_all(
                        "SELECT conversation_id FROM msg_participants "
                        "WHERE user_id = ?",
                        (user_id,),
                    )
                    or []
                )
                if _row_value(row, "conversation_id")
            ]

            # The upload's stored object is only half covered by the row above:
            # it is proved to exist now, and proved to be gone afterwards. A
            # "does it exist" helper that always answered no would fail here
            # instead of quietly passing the removal check.
            media_rows = [
                {
                    "backend": str(_row_value(row, "storage_backend") or "local"),
                    "path": str(_row_value(row, "storage_path") or ""),
                }
                for row in (
                    db.fetch_all(
                        "SELECT storage_backend, storage_path FROM media_files "
                        "WHERE uploaded_by = ?",
                        (user_id,),
                    )
                    or []
                )
            ]
            media_module = None
            try:
                from src.core import media as media_module
            except Exception as media_error:
                logger.warning(
                    "Purge-residue check: media module unavailable: %s", media_error
                )
            stored_objects = []
            if media_module is not None:
                for item in media_rows:
                    if item["path"] and media_module.storage_object_exists(
                        item["backend"], item["path"]
                    ):
                        stored_objects.append(item)
            self._purge_residue_result(
                "purge_residue_storage_object_present",
                "GET",
                "/api/v1/channels/{channel_id}/attachments",
                bool(stored_objects),
                200,
                0.0,
                ""
                if stored_objects
                else "the seeded upload is not in storage, so its removal cannot be proved",
            )

            # 3. The purge itself, through the endpoint an operator would use.
            purge_start = time.time()
            purge_resp = admin_session.post(
                f"{base}{purge_path.format(user_id=user_id)}", timeout=60
            )
            purge_ok = 200 <= purge_resp.status_code < 300
            self._purge_residue_result(
                "purge_residue_force_purge",
                "POST",
                purge_path,
                purge_ok,
                purge_resp.status_code,
                (time.time() - purge_start) * 1000,
                "" if purge_ok else purge_resp.text[:160],
            )
            if not purge_ok:
                return

            # 4. Read the database back. The account row, everything it owned
            #    and every conversation it was alone in has to be gone.
            leftovers: Dict[str, Any] = {}
            if db.fetch_one("SELECT id FROM auth_users WHERE id = ?", (user_id,)):
                leftovers["auth_users"] = user_id
            if server_id and db.fetch_one(
                "SELECT id FROM srv_servers WHERE id = ?", (server_id,)
            ):
                leftovers["srv_servers"] = server_id
            if channel_id and db.fetch_one(
                "SELECT id FROM srv_channels WHERE id = ?", (channel_id,)
            ):
                leftovers["srv_channels"] = channel_id
            for table, column in (
                ("auth_sessions", "user_id"),
                ("srv_members", "user_id"),
                ("msg_participants", "user_id"),
                ("media_files", "uploaded_by"),
            ):
                row = db.fetch_one(
                    f"SELECT count(*) AS n FROM {table} WHERE {column} = ?",
                    (user_id,),
                )
                remaining = int(_row_value(row, "n") or 0)
                if remaining:
                    leftovers[table] = remaining

            # A conversation this account was alone in must be erased; one it
            # shared survives for the remaining participant. Anything left
            # without a participant is the orphan the purge has to prevent.
            orphans = []
            for conversation_id in {
                cid for cid in [channel_conversation_id, *owned_conversations] if cid
            }:
                if not db.fetch_one(
                    "SELECT id FROM msg_conversations WHERE id = ?",
                    (conversation_id,),
                ):
                    continue
                other = db.fetch_one(
                    "SELECT count(*) AS n FROM msg_participants "
                    "WHERE conversation_id = ? AND user_id != ?",
                    (conversation_id, user_id),
                )
                if int(_row_value(other, "n") or 0) == 0:
                    orphans.append(conversation_id)
            if orphans:
                leftovers["orphaned_conversations"] = orphans

            self._purge_residue_result(
                "purge_residue_left_no_rows",
                "GET",
                f"/api/v1/admin/users/{user_id}",
                not leftovers,
                200,
                0.0,
                json.dumps(leftovers) if leftovers else "",
            )

            # The stored object has to go with the row, not just the row.
            if media_module is None:
                self._purge_residue_result(
                    "purge_residue_left_no_stored_objects",
                    "GET",
                    "/api/v1/channels/{channel_id}/attachments",
                    False,
                    0,
                    0.0,
                    "media module unavailable: storage state could not be checked",
                )
            else:
                surviving_objects = [
                    item["path"]
                    for item in stored_objects
                    if media_module.storage_object_exists(item["backend"], item["path"])
                ]
                self._purge_residue_result(
                    "purge_residue_left_no_stored_objects",
                    "GET",
                    "/api/v1/channels/{channel_id}/attachments",
                    not surviving_objects,
                    200,
                    0.0,
                    json.dumps(surviving_objects) if surviving_objects else "",
                )

            # 5. The shared DM is not residue: its other participant keeps it.
            messages_resp = admin_session.get(
                f"{base}/api/v1/channels/{dm_id}/messages", timeout=10
            )
            spared_ok = messages_resp.status_code == 200
            self._purge_residue_result(
                "purge_residue_spares_shared_dm",
                "GET",
                "/api/v1/channels/{channel_id}/messages",
                spared_ok,
                messages_resp.status_code,
                0.0,
                "" if spared_ok else messages_resp.text[:160],
            )
            # This run made the DM, so close it rather than leave the check's
            # own fixture on the deployment.
            try:
                admin_session.delete(f"{base}/api/v1/channels/{dm_id}", timeout=10)
            except Exception:
                pass
        except Exception as e:
            self._purge_residue_result(
                "purge_residue_seed",
                "POST",
                "/api/v1/servers/",
                False,
                error=f"{type(e).__name__}: {e}"[:200],
            )
        finally:
            try:
                doomed.close()
            except Exception:
                pass
            # A check that failed half-way must not leave its fixture account
            # behind; the purge endpoint is the same path the check trusts.
            if user_id:
                try:
                    if db.fetch_one(
                        "SELECT id FROM auth_users WHERE id = ?", (user_id,)
                    ):
                        admin_session.post(
                            f"{base}{purge_path.format(user_id=user_id)}", timeout=60
                        )
                except Exception as cleanup_error:
                    logger.warning(
                        "Purge-residue check: fixture cleanup failed for %s: %s",
                        user_id,
                        cleanup_error,
                    )

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

        # Step 5: admin lifecycle on a second request (detail -> approve
        # -> generate). Uses its own row because approve/generate move the
        # first request out of the cancellable states.
        logger.info("Testing DSAR admin lifecycle (second request)...")
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/users/@me/data-export",
            json={"password": test_pass, "format": "json"},
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            admin_id = None
            try:
                import src.api as _api3

                _db3 = _api3.get_db()
                if _db3:
                    _row3 = _db3.fetch_one(
                        "SELECT id FROM dsar_requests WHERE user_id = ? "
                        "AND status = 'pending' ORDER BY requested_at DESC LIMIT 1",
                        (self.ctx.test_user_id,),
                    )
                    if _row3:
                        admin_id = int(
                            _row3["id"] if isinstance(_row3, dict) else _row3[0]
                        )
            except Exception:
                pass
            if admin_id:
                base = self.ctx.base_url
                for method, path, label in [
                    (
                        "GET",
                        f"/api/v1/admin/data-export/{admin_id}",
                        "dsar_admin_get",
                    ),
                    (
                        "POST",
                        f"/api/v1/admin/data-export/{admin_id}/approve",
                        "dsar_admin_approve",
                    ),
                    (
                        "POST",
                        f"/api/v1/admin/data-export/{admin_id}/generate",
                        "dsar_admin_generate",
                    ),
                ]:
                    try:
                        if method == "GET":
                            r = session.get(f"{base}{path}", timeout=15)
                        else:
                            r = session.post(f"{base}{path}", json={}, timeout=60)
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
                            logger.warning(
                                f"DSAR {label} -> {r.status_code}: {r.text[:200]}"
                            )
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

        # Step 6: deny flow on a third request (deny requires pending, so
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
        applies 001 for real (the rollback step needs it applied); both are
        approval-gated: the real multi-admin ceremony precedes each.
        """
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.session.headers.get("Authorization"):
            logger.debug("Skipping new migration tests (no auth token)")
            return

        session = self.ctx.session
        mig_run = "/api/v1/admin/migrations/001/run"
        mig_rollback = "/api/v1/admin/migrations/001/rollback"

        # --- Run (apply) migration 001 via new endpoint ---
        # Applied for real (not dry-run): the rollback step below needs an
        # applied 001 to roll back, and the legacy test may have left it
        # rolled back. Net DB effect across both tests is zero.
        logger.info("Testing POST /api/v1/admin/migrations/001/run (apply)...")
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
            json={"dry_run": False},
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
