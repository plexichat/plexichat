"""Feature setup mixin.

Creates all feature-specific test resources: settings, applications,
bots, notifications, reports, automod rules, access tokens, tickets,
polls, friend requests, emoji, stickers, and threads.
"""

import secrets
import time

import src.api as api
import utils.logger as logger

from .base import SetupServiceBase


class FeatureSetupMixin(SetupServiceBase):
    """Creates feature-specific test resources."""

    def create_test_settings(self) -> None:
        settings_mod = api.get_settings()
        if not settings_mod:
            return
        try:
            settings_mod.set_setting(self.ctx.test_user_id, "test_key", "test_value")
            logger.debug("Created test setting 'test_key'")
        except Exception as e:
            logger.warning(f"Failed to create test setting: {e}")

    def create_test_application_and_bot(self) -> None:
        applications_mod = api.get_applications()
        if not applications_mod:
            return
        try:
            app_name = f"Self-Test App {secrets.token_hex(4)}"
            app = applications_mod.create_application(self.ctx.test_user_id, app_name)
            self.ctx.test_application_id = app.id
            logger.debug(f"Created test application ID: {self.ctx.test_application_id}")
            try:
                bot = applications_mod.create_bot_for_application(
                    self.ctx.test_user_id, self.ctx.test_application_id
                )
                self.ctx.test_bot_id = (
                    bot["bot_id"] if isinstance(bot, dict) else bot.id
                )
                logger.debug(f"Created test bot ID: {self.ctx.test_bot_id}")
            except Exception as e:
                if type(e).__name__ == "UserExistsError" or "already registered" in str(
                    e
                ):
                    logger.warning(
                        f"Test bot already exists; reusing existing bot context: {e}"
                    )
                else:
                    logger.warning(f"Failed to create test bot: {e}")
        except Exception as e:
            logger.warning(f"Failed to create test application: {e}")

        if self.ctx.test_application_id:
            try:
                applications_mod.update_bot_profile(
                    application_id=self.ctx.test_application_id,
                    user_id=self.ctx.test_user_id,
                    description="Self-test bot profile",
                )
                logger.debug(
                    f"Created bot profile for app {self.ctx.test_application_id}"
                )
            except Exception as e2:
                logger.warning(f"Failed to create bot profile: {e2}")

    def create_test_notification(self) -> None:
        _n_db = api.get_db()
        if not _n_db:
            return
        try:
            existing_notif = _n_db.fetch_one(
                "SELECT id FROM notif_notifications WHERE user_id = ?",
                (self.ctx.test_user_id,),
            )
            if not existing_notif:
                notif_id = self.ctx.data.generate_snowflake()
                _n_db.execute(
                    "INSERT INTO notif_notifications (id, user_id, sender_id, message_id, conversation_id, mention_type, content_preview, read, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        notif_id,
                        self.ctx.test_user_id,
                        self.ctx.test_user_id,
                        0,
                        0,
                        "user",
                        "Self-test notification body",
                        0,
                        int(time.time()),
                    ),
                )
                self.ctx.test_notification_id = notif_id
                logger.debug("Created test notification")
            else:
                self.ctx.test_notification_id = (
                    existing_notif["id"]
                    if isinstance(existing_notif, dict)
                    else existing_notif[0]
                )
        except Exception as e:
            logger.warning(f"Failed to create test notification: {e}")

    def create_test_reports(self) -> None:
        _r_db = api.get_db()
        if not _r_db:
            return
        try:
            existing_report = _r_db.fetch_one(
                "SELECT id FROM user_reports WHERE reporter_id = ?",
                (self.ctx.test_user_id,),
            )
            if not existing_report and self.ctx.test_other_user_id:
                report_id = self.ctx.data.generate_snowflake()
                _r_db.execute(
                    "INSERT INTO user_reports (id, reporter_id, reported_user_id, reason, category, status, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        report_id,
                        self.ctx.test_user_id,
                        self.ctx.test_other_user_id,
                        "Self-test report",
                        "other",
                        "open",
                        int(time.time()),
                    ),
                )
                logger.debug("Created test user report")
                try:
                    _r_db.execute(
                        "INSERT INTO reports (id, reporter_id, report_type, target_id, reason, category, status, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            report_id,
                            self.ctx.test_user_id,
                            "user",
                            self.ctx.test_other_user_id,
                            "Self-test report",
                            "other",
                            "open",
                            "medium",
                            int(time.time()),
                            int(time.time()),
                        ),
                    )
                    logger.debug("Synced report to reports table for PATCH status test")
                except Exception:
                    pass
                hash_report_id = self.ctx.data.generate_snowflake()
                _r_db.execute(
                    "INSERT OR IGNORE INTO media_hash_reports (id, reporter_id, hash_value, reason, status, reported_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        hash_report_id,
                        self.ctx.test_user_id,
                        "a" * 64,
                        "Self-test hash report",
                        "open",
                        int(time.time()),
                    ),
                )
                self.ctx.test_hash_report_id = hash_report_id
                logger.debug("Created test hash report")
                if self.ctx.test_message_id and self.ctx.test_other_user_id:
                    msg_report_id = self.ctx.data.generate_snowflake()
                    _r_db.execute(
                        "INSERT OR IGNORE INTO message_reports (id, reporter_id, message_id, channel_id, reported_user_id, reason, category, status, reported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            msg_report_id,
                            self.ctx.test_user_id,
                            self.ctx.test_message_id,
                            self.ctx.test_channel_id or 0,
                            self.ctx.test_other_user_id,
                            "Self-test message report",
                            "other",
                            "open",
                            int(time.time()),
                        ),
                    )
                    self.ctx.test_message_report_id = msg_report_id
                    logger.debug("Created test message report")
                self.ctx.test_report_id = report_id
        except Exception as e:
            logger.warning(f"Failed to create test reports: {e}")

    def create_test_automod_rule(self) -> None:
        if not self.ctx.test_server_id:
            return
        try:
            from src.core import automod
            from src.core.automod.models import RuleType

            assert self.ctx.test_user_id is not None
            assert self.ctx.test_server_id is not None
            automod_rule = automod.create_rule(
                user_id=self.ctx.test_user_id,
                server_id=self.ctx.test_server_id,
                name="Self-Test AutoMod Rule",
                rule_type=RuleType.KEYWORD,
                rule_config={"keywords": ["test-bad-word"]},
                actions=[{"type": "delete_message", "config": {}}],
                priority=0,
                check_all=False,
            )
            self.ctx.test_automod_rule_id = automod_rule.id
            logger.debug(
                f"Created test automod rule ID: {self.ctx.test_automod_rule_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to create test automod rule: {e}")

    def create_test_access_token(self) -> None:
        try:
            from src.core import auth as auth_module

            token_name = f"selftest-token-{secrets.token_hex(4)}"
            access_token = auth_module.create_api_access_token(
                name=token_name,
                created_by=self.ctx.test_user_id,
                token_value=secrets.token_urlsafe(48),
                description="Self-test access token",
                scope_mode="monitor",
            )
            self.ctx.test_access_token_id = access_token.id
            logger.debug(
                f"Created test access token ID: {self.ctx.test_access_token_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to create test access token: {e}")

    def create_test_ticket(self) -> None:
        if not self.ctx.test_other_user_id:
            return
        try:
            from src.core import feedback as feedback_module

            ticket_id = feedback_module.submit_feedback(
                user_id=self.ctx.test_other_user_id,
                content="Self-test support ticket",
                category="bug",
                rating=5,
            )
            self.ctx.test_ticket_id = ticket_id
            logger.debug(f"Created test ticket ID: {self.ctx.test_ticket_id}")
        except Exception as e:
            logger.warning(f"Failed to create test ticket: {e}")

    def create_test_poll(self) -> None:
        if not self.ctx.test_conversation_id:
            return
        messaging = api.get_messaging()
        if not messaging:
            return
        try:
            from src.core import polls as polls_module
            from src.core.polls import PollResultsVisibility

            poll_parent = messaging.send_message(
                self.ctx.test_user_id,
                self.ctx.test_conversation_id,
                "Self-test poll parent message",
            )
            poll_msg_id = poll_parent.id
            assert self.ctx.test_user_id is not None
            poll = polls_module.create_poll(
                user_id=self.ctx.test_user_id,
                message_id=poll_msg_id,
                question="Self-test poll question?",
                options=["Option A", "Option B", "Option C"],
                duration_hours=24,
                allow_multiple_choice=True,
                results_visibility=PollResultsVisibility.ALWAYS,
            )
            self.ctx.test_poll_id = poll.id
            self.ctx.test_poll_option_ids = [opt.id for opt in poll.options]
            logger.debug(f"Created test poll ID: {self.ctx.test_poll_id}")
        except Exception as e:
            logger.warning(f"Failed to create test poll: {e}")

    def create_test_friend_request(self) -> None:
        if not self.ctx.test_other_user_id or not self.ctx.test_user_id:
            return
        fr_url = f"{self.ctx.base_url}/api/v1/relationships"
        fr_payload = {
            "user_id": self.ctx.test_user_id,
            "message": "Self-test friend request",
        }
        try:
            fr_resp = self.ctx.other_session.post(fr_url, json=fr_payload, timeout=10)
            logger.info(
                f"Friend request POST {fr_url} -> {fr_resp.status_code} {fr_resp.text[:300]}"
            )
            if fr_resp.status_code in (200, 201):
                _fr_lookup = api.get_db()
                if _fr_lookup:
                    _fr_row = _fr_lookup.fetch_one(
                        "SELECT id FROM rel_friend_requests WHERE sender_id = ? AND recipient_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                        (self.ctx.test_other_user_id, self.ctx.test_user_id),
                    )
                    if _fr_row:
                        self.ctx.test_friend_request_id = (
                            int(_fr_row["id"])
                            if isinstance(_fr_row, dict)
                            else int(_fr_row[0])
                        )
                        logger.info(
                            f"Friend request created (id={self.ctx.test_friend_request_id})"
                        )
                    else:
                        logger.warning(
                            "POST /relationships succeeded (200) but no pending request found in DB"
                        )
                else:
                    logger.warning("DB not available for friend request lookup")
            elif fr_resp.status_code == 409:
                logger.warning("Friend request already exists (409); re-fetching")
                _fr_db = api.get_db()
                if _fr_db:
                    _fr_row = _fr_db.fetch_one(
                        "SELECT id FROM rel_friend_requests WHERE sender_id = ? AND recipient_id = ? AND status = 'pending'",
                        (self.ctx.test_other_user_id, self.ctx.test_user_id),
                    )
                    if _fr_row:
                        self.ctx.test_friend_request_id = (
                            int(_fr_row["id"])
                            if isinstance(_fr_row, dict)
                            else int(_fr_row[0])
                        )
                        logger.info(
                            f"Found existing friend request (id={self.ctx.test_friend_request_id})"
                        )
                    else:
                        logger.warning(
                            "409 returned but no pending request found in DB"
                        )
            else:
                logger.warning(
                    f"Friend request failed: {fr_resp.status_code} {fr_resp.text[:300]}"
                )
        except Exception as e:
            logger.warning(f"Failed to create friend request: {e}")

    def create_test_emoji(self) -> None:
        if not self.ctx.test_server_id:
            return
        try:
            db_e = api.get_db()
            if db_e:
                existing_emoji = db_e.fetch_one(
                    "SELECT id FROM react_custom_emoji WHERE name = ? AND server_id = ?",
                    ("selftest_emoji", self.ctx.test_server_id),
                )
                if not existing_emoji:
                    emoji_id = self.ctx.data.generate_snowflake()
                    db_e.execute(
                        "INSERT INTO react_custom_emoji (id, name, server_id, created_by, animated, url, size, width, height, available, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            emoji_id,
                            "selftest_emoji",
                            self.ctx.test_server_id,
                            self.ctx.test_user_id,
                            0,
                            "https://example.com/emoji.png",
                            2048,
                            128,
                            128,
                            1,
                            int(time.time()),
                        ),
                    )
                    self.ctx.test_emoji_id = emoji_id
                    logger.debug(f"Created test emoji ID: {self.ctx.test_emoji_id}")
                else:
                    self.ctx.test_emoji_id = (
                        existing_emoji["id"]
                        if isinstance(existing_emoji, dict)
                        else existing_emoji[0]
                    )
        except Exception as e:
            logger.warning(f"Failed to create test emoji: {e}")

    def create_test_sticker(self) -> None:
        if not self.ctx.test_server_id:
            return
        try:
            db_s = api.get_db()
            if db_s:
                existing_pack = db_s.fetch_one(
                    "SELECT id FROM sticker_packs WHERE name = ?",
                    ("selftest_pack",),
                )
                if existing_pack:
                    pack_id = (
                        existing_pack["id"]
                        if isinstance(existing_pack, dict)
                        else existing_pack[0]
                    )
                else:
                    pack_id = self.ctx.data.generate_snowflake()
                    db_s.execute(
                        "INSERT INTO sticker_packs (id, name, description_encrypted, pack_type, server_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            pack_id,
                            "selftest_pack",
                            "Self-test pack",
                            "server",
                            self.ctx.test_server_id,
                            self.ctx.test_user_id,
                            int(time.time()),
                            int(time.time()),
                        ),
                    )
                existing_sticker = db_s.fetch_one(
                    "SELECT id FROM sticker_stickers WHERE name = ?",
                    ("selftest_sticker",),
                )
                if not existing_sticker:
                    sticker_id = self.ctx.data.generate_snowflake()
                    try:
                        db_s.execute(
                            "INSERT INTO sticker_stickers (id, name, pack_id, format, description, tags, url, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                sticker_id,
                                "selftest_sticker",
                                pack_id,
                                "png",
                                "Self-test sticker",
                                '["test"]',
                                "https://example.com/sticker.png",
                                1024,
                                int(time.time()),
                            ),
                        )
                    except Exception:
                        db_s.execute(
                            "INSERT INTO sticker_stickers (id, name, pack_id, format, tags, url, size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                sticker_id,
                                "selftest_sticker",
                                pack_id,
                                "png",
                                '["test"]',
                                "https://example.com/sticker.png",
                                1024,
                                int(time.time()),
                            ),
                        )
                    self.ctx.test_sticker_id = sticker_id
                    logger.debug(f"Created test sticker ID: {self.ctx.test_sticker_id}")
                else:
                    self.ctx.test_sticker_id = (
                        existing_sticker["id"]
                        if isinstance(existing_sticker, dict)
                        else existing_sticker[0]
                    )
        except Exception as e:
            logger.warning(f"Failed to create test sticker: {e}")

    def create_test_thread(self) -> None:
        if not self.ctx.test_channel_id or not self.ctx.test_message_id:
            return
        try:
            threads_mod = api.get_threads()
            if threads_mod:
                from src.core.threads import AutoArchiveDuration

                thread = threads_mod.create_thread_from_message(
                    user_id=self.ctx.test_user_id,
                    message_id=self.ctx.test_message_id,
                    name="Self-Test Thread",
                    auto_archive_duration=AutoArchiveDuration.ONE_HOUR,
                )
                self.ctx.test_thread_id = thread.id
                logger.debug(f"Created test thread ID: {self.ctx.test_thread_id}")
        except Exception as e:
            logger.warning(f"Failed to create test thread: {e}")
