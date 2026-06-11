"""Interaction callback endpoint tester mixin.

Tests the interaction callback flow by seeding an interaction directly
in the database and then calling the callback endpoint.
"""

import time
import hashlib
import secrets

import src.api as api
import utils.logger as logger

from .base import EndpointTesterBase


class InteractionMixin(EndpointTesterBase):
    """Tests interaction callback API endpoints."""

    def test_interaction_callback(self) -> None:
        """Test interaction response callback by seeding a DB interaction."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_application_id:
            logger.debug("Skipping interaction callback (no application_id)")
            return
        if not self.ctx.test_server_id or not self.ctx.test_channel_id:
            logger.debug("Skipping interaction callback (no server/channel)")
            return
        if not self.ctx.test_user_id:
            logger.debug("Skipping interaction callback (no user_id)")
            return

        session = self.ctx.session
        db = api.get_db()
        if not db:
            logger.debug("Skipping interaction callback (no database)")
            return

        # Seed an interaction directly in the database
        interaction_id = int(time.time() * 1000) % (2**53)
        random_bytes = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(random_bytes.encode()).hexdigest()
        full_token = f"int.{interaction_id}.{random_bytes}"
        now = int(time.time() * 1000)
        interaction_type = 2  # APPLICATION_COMMAND type

        try:
            db.execute(
                """INSERT OR IGNORE INTO app_interactions
                   (id, application_id, interaction_type, data, server_id, channel_id,
                    user_id, token_hash, version, created_at, responded)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    interaction_id,
                    int(self.ctx.test_application_id),
                    interaction_type,
                    None,
                    int(self.ctx.test_server_id),
                    int(self.ctx.test_channel_id),
                    int(self.ctx.test_user_id),
                    token_hash,
                    1,
                    now,
                    0,
                ),
            )
        except Exception as e:
            logger.warning(f"Could not seed interaction in DB: {e}")
            return

        logger.info(f"Seeded interaction {interaction_id} in DB")

        # Call the callback endpoint
        logger.info("Testing POST /api/v1/applications/interactions/.../callback...")
        start = time.time()
        resp = session.post(
            f"{self.ctx.base_url}/api/v1/applications/interactions/{full_token}/callback",
            json={"type": 4, "data": {"content": "Pong from selftest!"}},
            timeout=5,
        )
        duration = (time.time() - start) * 1000
        success = resp.status_code in (200, 204)
        self.ctx.results.append(
            {
                "method": "POST",
                "path": "/api/v1/applications/interactions/.../callback",
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "label": "interaction_callback",
            }
        )
        if success:
            logger.info(f"Interaction callback PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Interaction callback -> {resp.status_code}: {resp.text[:200]}"
            )
