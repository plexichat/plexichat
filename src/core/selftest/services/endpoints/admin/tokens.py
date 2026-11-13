import utils.logger as logger

from .base import AdminMixinBase


class TokenMixin(AdminMixinBase):
    """Access-token rotation coverage."""

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
        except Exception as parse_error:
            logger.debug(f"rotated token id parse failed: {parse_error}")
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
