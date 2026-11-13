import time

from .base import DataGeneratorBase


class ParamValueMixin(DataGeneratorBase):
    """Resolve path and query parameters to concrete self-test IDs."""

    def get_param_value(self, p_name: str, path: str) -> str:
        name_low = p_name.lower()

        def _gen_snowflake():
            return str(self.generate_snowflake())

        val = "1"
        if "badge" in name_low:
            val = "partner"
        elif "username" in name_low:
            val = "selftest_admin"
        elif (
            name_low == "target_admin_id"
            or name_low == "admin_id"
            or name_low.startswith("target_admin_")
        ):
            # Admin-to-admin endpoint target (e.g.
            # ``/api/v1/admin/auth/force-password-change/{target_admin_id}``).
            # The main self-test session authenticates as test_user_id, so we
            # resolve to ``test_admin_user_id`` — a different admin row
            # seeded by ``setup_other_admin_user`` — to satisfy both the
            # ``exists in admin_users`` lookup and the ``acting != target``
            # self-target refusal in ``force_password_change_target``.
            #
            # IMPORTANT: this branch MUST NOT have an inner ``else: val =
            # test_user_id`` override, because the route can still hit the
            # acting==target refusal if a fallback defaults to the acting
            # admin.  Keeping this branch minimal and monotonic to keep the
            # invariant intact under edit.
            val = (
                str(self.ctx.test_admin_user_id)
                if self.ctx.test_admin_user_id
                else _gen_snowflake()
            )
        elif "user" in name_low or "member" in name_low:
            if (
                "/force-purge" in path
                or "/force-logout" in path
                or "/force-username-change" in path
                or "/lock-user" in path
                or "/unlock-user" in path
                or "/toggle-status" in path
            ):
                val = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else _gen_snowflake()
                )
            elif "/admin-users" in path:
                # Use other_user_id for managing a DIFFERENT admin (avoids self-management 403)
                val = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else str(self.ctx.test_user_id)
                )
            elif "/bans/" in path or "/kick" in path:
                val = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else _gen_snowflake()
                )
            elif "/relationships/" in path and "/accept" in path:
                # The route is PUT /relationships/{user_id}/accept.
                # {user_id} is the SENDER of the friend request we want to accept.
                # Setup creates the request from other_user -> admin,
                # so admin (main session) accepts other_user's request.
                # The path param must be the sender's user_id = other_user_id.
                val = str(
                    self.ctx.test_other_user_id
                    if self.ctx.test_other_user_id
                    else self.generate_snowflake()
                )
            elif "/threads/" in path and "/members/" in path:
                # Threads add-member must target a server member. other_user
                # is a member (idempotent 200); the third user is not, so
                # prefer the member identity.
                if self.ctx.test_other_user_id:
                    val = str(self.ctx.test_other_user_id)
                else:
                    val = _gen_snowflake()
            elif "/invites/" in path or "reports/users" in path:
                val = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else _gen_snowflake()
                )
            elif "/blocked-users" in path:
                # Moderation hierarchy refuses targets holding any admin
                # identity; target the permanently unprivileged fourth user
                # (falls back to a snowflake when absent).
                val = str(
                    getattr(self.ctx, "test_fourth_user_id", None) or _gen_snowflake()
                )
            else:
                val = str(self.ctx.test_user_id)
        elif name_low in ("conversation_id", "conversationid"):
            val = str(self.ctx.test_conversation_id or _gen_snowflake())
        elif "server" in name_low or "guild" in name_low:
            val = str(self.ctx.test_server_id)
        elif "channel" in name_low:
            val = str(self.ctx.test_channel_id)
        elif "violation" in name_low:
            val = str(self.ctx.test_automod_violation_id or _gen_snowflake())
        elif "rule" in name_low or "automod" in name_low:
            val = str(self.ctx.test_automod_rule_id or _gen_snowflake())
        elif "ticket" in name_low:
            val = str(self.ctx.test_ticket_id or _gen_snowflake())
        elif "token" in name_low and ("access" in path or "security" in path):
            val = str(self.ctx.test_access_token_id or _gen_snowflake())
        elif "device" in name_low and "id" in name_low:
            val = str(getattr(self.ctx, "test_device_id", None) or _gen_snowflake())
        elif "thread" in name_low:
            val = str(self.ctx.test_thread_id or _gen_snowflake())
        elif "poll" in name_low:
            val = str(self.ctx.test_poll_id or _gen_snowflake())
        elif "role" in name_low:
            if "/admin/" in path:
                if "/admin/roles/" in path and (
                    "/roles/" + str(self.ctx.test_admin_role_id) in path
                    or "/roles/" + str(self.ctx._admin_role_super_id) in path
                ):
                    val = str(
                        self.ctx.test_non_system_role_id
                        or self.ctx.test_admin_role_id
                        or _gen_snowflake()
                    )
                else:
                    val = str(
                        self.ctx.test_non_system_role_id
                        or self.ctx.test_admin_role_id
                        or self.ctx._admin_role_super_id
                        or _gen_snowflake()
                    )
            else:
                val = str(self.ctx.test_role_id or _gen_snowflake())
        elif "invite" in name_low or "code" in name_low:
            val = self.ctx.test_invite_code or "test_invite"
        elif name_low == "url" and "/media/proxy" in path:
            # The media proxy endpoint resolves query param ``url`` to a real
            # public http(s) URL. ``1`` (the generic default) would be rejected
            # with 400 by the route's scheme validation. Use an asset served
            # from a domain we control (plexichat.com / plexichat-landing) so
            # the proxy actually fetches and returns 200.
            val = "https://plexichat.com/image.png"
        elif "webhook" in name_low:
            val = (
                str(self.ctx.test_webhook_id)
                if self.ctx.test_webhook_id
                else _gen_snowflake()
            )
        elif "token" in name_low and "webhook" in path:
            val = self.ctx.test_webhook_token or "test_token"
        elif "interaction_token" in name_low:
            val = "test_interaction_token"
        elif "passkey_id" in name_low:
            val = _gen_snowflake()
        elif "key" in name_low:
            val = "test_key"
        elif "message" in name_low:
            val = (
                str(self.ctx.test_message_id)
                if self.ctx.test_message_id
                else _gen_snowflake()
            )
        elif "filename" in name_low:
            if "/admin/logs/" in path:
                val = self.ctx.test_log_filename or "test_file.png"
            else:
                val = "test_file.png"
        elif "session" in name_low:
            val = "test_session"
        elif "hash" in name_low:
            val = "a" * 64
        elif "emoji" in name_low:
            if "/reactions/" in path:
                # Reaction endpoints need a valid unicode emoji (numeric
                # ids 400 as invalid by design); custom-emoji detail is
                # covered by the lifecycle test.
                val = "👍"
            elif "id" in name_low or "name" not in name_low:
                val = str(self.ctx.test_emoji_id or _gen_snowflake())
            else:
                val = "smile"
        elif "sticker" in name_low:
            val = str(self.ctx.test_sticker_id or _gen_snowflake())
        elif "sound" in name_low:
            val = str(getattr(self.ctx, "test_soundboard_id", None) or _gen_snowflake())
        elif "application" in name_low or "app_id" in name_low:
            val = str(self.ctx.test_application_id or _gen_snowflake())
        elif "notification" in name_low:
            val = (
                str(self.ctx.test_notification_id)
                if self.ctx.test_notification_id
                else _gen_snowflake()
            )
        elif "request" in name_low and "bots" in path:
            val = str(
                self.ctx.test_bot_request_id
                or self.ctx.test_bot_id
                or self.ctx.test_application_id
                or _gen_snowflake()
            )
        elif "approval" in name_low:
            val = str(self.ctx.test_approval_id or _gen_snowflake())
        elif "request_id" in name_low and "data-export" in path:
            val = str(self.ctx.test_dsar_id or _gen_snowflake())
        elif name_low == "feature" and "/admin/artifacts/features/" in path:
            val = "plexiscribe"
        elif "report" in name_low:
            if "id" in name_low:
                if "hash-report" in path or "hash_report" in path:
                    val = str(
                        self.ctx.test_hash_report_id
                        or self.ctx.test_report_id
                        or _gen_snowflake()
                    )
                elif "message-report" in path or "message_report" in path:
                    val = str(
                        self.ctx.test_message_report_id
                        or self.ctx.test_report_id
                        or _gen_snowflake()
                    )
                elif "user-report" in path or "user_report" in path:
                    val = str(self.ctx.test_report_id or _gen_snowflake())
                else:
                    val = str(self.ctx.test_report_id or _gen_snowflake())
        elif "version" in name_low and "/migrations/" in path:
            val = "001"
        elif "format" in name_low and ("/audit/" in path or "/telemetry/" in path):
            val = "csv"
        elif "deletion_at" in name_low:
            val = str(int(time.time()) + 86400)
        elif name_low == "export_id":
            # Transcript export ids are minted server-side; resolve to the
            # export seeded during setup so status/download endpoints return 200.
            val = (
                str(self.ctx.test_export_id)
                if self.ctx.test_export_id
                else _gen_snowflake()
            )
        elif "connection" in name_low and "plexijoin" in path:
            val = str(self.ctx.test_plexijoin_connection_id or _gen_snowflake())
        elif "request" in name_low and "plexijoin" in path and "deny" in path:
            val = str(self.ctx.test_plexijoin_deny_request_id or _gen_snowflake())
        elif "request" in name_low and "plexijoin" in path:
            val = str(self.ctx.test_plexijoin_request_id or _gen_snowflake())
        elif (
            "id" in name_low
            or name_low.endswith("_id")
            or name_low in ("around", "before", "after")
        ):
            if "automod" in path or "rule" in name_low:
                val = str(self.ctx.test_automod_rule_id or _gen_snowflake())
            elif "ticket" in path:
                val = str(self.ctx.test_ticket_id or _gen_snowflake())
            elif "access-token" in path or "access_token" in name_low:
                val = str(self.ctx.test_access_token_id or _gen_snowflake())
            elif "thread" in path:
                val = str(self.ctx.test_thread_id or _gen_snowflake())
            elif "poll" in path:
                val = str(self.ctx.test_poll_id or _gen_snowflake())
            elif "server" in path:
                val = str(self.ctx.test_server_id)
            elif "channel" in path:
                val = str(self.ctx.test_channel_id)
            elif "relationship" in path and "/accept" in path:
                # Same logic as above: param is the sender's user_id
                val = str(self.ctx.test_user_id)
            elif "user" in path:
                val = str(self.ctx.test_user_id)
            elif "message" in path or name_low in ("around", "before", "after"):
                val = str(self.ctx.test_message_id or _gen_snowflake())
            elif "role" in name_low:
                if "/admin/" in path and "/roles/" in path:
                    val = str(
                        self.ctx.test_admin_role_id
                        or self.ctx._admin_role_super_id
                        or _gen_snowflake()
                    )
                else:
                    val = str(self.ctx.test_role_id or _gen_snowflake())
            elif "relationship" in path:
                if "/accept" in path and self.ctx.test_friend_request_id:
                    val = str(self.ctx.test_friend_request_id)
                else:
                    val = str(self.ctx.test_user_id)
            elif "report" in path:
                if "hash-report" in path or "hash_report" in path:
                    val = str(
                        self.ctx.test_hash_report_id
                        or self.ctx.test_report_id
                        or _gen_snowflake()
                    )
                elif "message-report" in path or "message_report" in path:
                    val = str(
                        self.ctx.test_message_report_id
                        or self.ctx.test_report_id
                        or _gen_snowflake()
                    )
                elif "user-report" in path or "user_report" in path:
                    val = str(self.ctx.test_report_id or _gen_snowflake())
                else:
                    val = str(self.ctx.test_report_id or _gen_snowflake())
            elif "notification" in path:
                val = str(self.ctx.test_notification_id or _gen_snowflake())
            elif "application" in name_low or "app" in name_low:
                val = str(self.ctx.test_application_id or _gen_snowflake())
            elif "bot" in path:
                val = str(
                    self.ctx.test_bot_id
                    or self.ctx.test_application_id
                    or _gen_snowflake()
                )
            elif "sticker" in path:
                val = str(self.ctx.test_sticker_id or _gen_snowflake())
            elif "artifact" in path and "attachment" not in name_low:
                val = str(self.ctx.test_artifact_id or _gen_snowflake())
            else:
                val = _gen_snowflake()
        return val
