"""Server setup mixin.

Creates the test server, channel, message, role, invite,
joins the other user, and creates a webhook.
"""

import secrets
import time

import src.api as api
import utils.logger as logger

from .base import SetupServiceBase


class ServerSetupMixin(SetupServiceBase):
    """Creates server-related test resources."""

    def create_server_and_channel(self) -> bool:
        servers_mod = api.get_servers()
        if not servers_mod:
            logger.error("Servers module not available for self-test")
            return False

        logger.info("Creating test server...")
        server = servers_mod.create_server(
            self.ctx.test_user_id,
            "Self-Test Server",
            "Temporary server for API testing",
        )
        self.ctx.test_server_id = server.id
        logger.info(f"Test server ID: {self.ctx.test_server_id}")

        logger.info("Creating test channel...")
        channel = servers_mod.create_channel(
            self.ctx.test_user_id, server.id, "test-channel"
        )
        self.ctx.test_channel_id = channel.id
        self.ctx.test_conversation_id = getattr(channel, "conversation_id", None)
        logger.info(
            f"Test channel ID: {self.ctx.test_channel_id}, Conv ID: {self.ctx.test_conversation_id}"
        )
        return True

    def create_test_message(self) -> None:
        if not self.ctx.test_conversation_id:
            return
        messaging = api.get_messaging()
        if not messaging:
            return
        logger.info("Creating test message...")
        try:
            msg = messaging.send_message(
                self.ctx.test_user_id,
                self.ctx.test_conversation_id,
                "Self-test reference message",
            )
            self.ctx.test_message_id = msg.id
            logger.info(f"Test message ID: {self.ctx.test_message_id}")
        except Exception as e:
            logger.warning(f"Failed to create test message: {e}")

    def create_test_attachment(self) -> None:
        if not self.ctx.test_message_id:
            return
        db = api.get_db()
        if not db:
            return
        logger.debug("Creating test attachment...")
        try:
            row = db.fetch_one(
                "SELECT id FROM msg_attachments WHERE message_id = ? LIMIT 1",
                (self.ctx.test_message_id,),
            )
            if row:
                self.ctx.test_attachment_id = int(row["id"])
                logger.debug(
                    f"Reusing test attachment ID: {self.ctx.test_attachment_id}"
                )
                return
            db.execute(
                "INSERT INTO msg_attachments "
                "(message_id, filename, content_type, size, url, created_at, deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    self.ctx.test_message_id,
                    "selftest_upload.txt",
                    "text/plain",
                    11,
                    "https://example.com/selftest_upload.txt",
                    int(time.time() * 1000),
                ),
            )
            aid = db.fetch_one(
                "SELECT id FROM msg_attachments WHERE message_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (self.ctx.test_message_id,),
            )
            if aid:
                self.ctx.test_attachment_id = int(aid["id"])
                logger.debug(f"Test attachment ID: {self.ctx.test_attachment_id}")
        except Exception as e:
            logger.warning(f"Failed to create test attachment: {e}")

    def create_test_export(self) -> None:
        if not self.ctx.test_conversation_id or not self.ctx.test_user_id:
            return
        logger.debug("Creating test transcript export...")
        try:
            from src.core.messaging.services.export import TranscriptExportService
            from src.core.messaging.repositories.message import MessageRepository
            from src.core.messaging.repositories.participant import (
                ParticipantRepository,
            )

            db = api.get_db()
            try:
                svc = TranscriptExportService(
                    db, MessageRepository(db), ParticipantRepository(db)
                )
                result = svc.request_export(
                    self.ctx.test_user_id,
                    self.ctx.test_conversation_id,
                    "json",
                )
                export_id = result.get("export_id")
                if export_id:
                    self.ctx.test_export_id = str(export_id)
                    logger.debug(f"Test export ID: {self.ctx.test_export_id}")
            finally:
                if db:
                    db.close()
        except Exception as e:
            logger.warning(f"Failed to create test export: {e}")

    def create_test_role(self) -> None:
        servers_mod = api.get_servers()
        if not servers_mod:
            return
        logger.debug("Creating test role...")
        try:
            role = servers_mod.create_role(
                self.ctx.test_user_id,
                self.ctx.test_server_id,
                f"Test Role {secrets.token_hex(4)}",
                color="#ff0000",
            )
            self.ctx.test_role_id = role.id
            logger.debug(f"Test role ID: {self.ctx.test_role_id}")
        except Exception as e:
            logger.warning(f"Failed to create test role: {e}")

    def create_test_invite(self) -> None:
        servers_mod = api.get_servers()
        if not servers_mod:
            return
        logger.debug("Creating test invite...")
        try:
            invite = servers_mod.create_invite(
                self.ctx.test_user_id, self.ctx.test_channel_id
            )
            self.ctx.test_invite_code = invite.code
        except Exception as e:
            logger.warning(f"Failed to create test invite: {e}")
            self.ctx.test_invite_code = None
        logger.debug(f"Test invite code: {self.ctx.test_invite_code}")

    def join_other_user_to_server(self) -> None:
        if not (
            self.ctx.test_other_user_id
            and self.ctx.test_server_id
            and self.ctx.test_invite_code
        ):
            return
        join_url = (
            f"{self.ctx.base_url}/api/v1/channels/invites/{self.ctx.test_invite_code}"
        )
        try:
            join_resp = self.ctx.other_session.post(join_url, timeout=10)
            logger.info(
                f"Server join POST {join_url} -> {join_resp.status_code} {join_resp.text[:300]}"
            )
            if join_resp.status_code not in (200, 201, 204):
                logger.warning(
                    f"Server join failed: {join_resp.status_code} {join_resp.text[:300]}"
                )
        except Exception as e:
            logger.warning(f"Failed to join other user to server: {e}")

    def create_test_webhook(self) -> None:
        webhooks_mod = api.get_webhooks()
        if not webhooks_mod:
            return
        logger.debug("Creating test webhook...")
        try:
            webhook = webhooks_mod.create_webhook(
                self.ctx.test_user_id, self.ctx.test_channel_id, "Self-Test Webhook"
            )
            self.ctx.test_webhook_id = webhook.id
            self.ctx.test_webhook_token = getattr(webhook, "token", None)
            logger.debug(f"Test webhook ID: {self.ctx.test_webhook_id}")
        except Exception as e:
            logger.warning(f"Failed to create test webhook: {e}")
