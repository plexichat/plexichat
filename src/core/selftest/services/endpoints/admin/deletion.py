import json
import secrets
import time
from typing import Any, Dict

import src.api as api
import utils.logger as logger

from .base import AdminMixinBase


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


class DeletionMixin(AdminMixinBase):
    """Delay-deletion and purge-residue coverage."""

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
