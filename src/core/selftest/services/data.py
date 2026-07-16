"""
Data generation service for SelfTestRunner.

Provides snowflake generation, OpenAPI schema-to-body conversion,
and path-parameter resolution with context-aware test-ID injection.
"""

import base64
import time
import secrets
import random
import re
from pathlib import Path
from typing import Dict, Any, Optional


from ..context import SelfTestContext


class DataGenerator:
    """Generates request bodies and resolves path/query parameters."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def generate_snowflake(self) -> int:
        return secrets.randbits(60) + 10**17

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
            elif "/invites/" in path or "reports/users" in path:
                val = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else _gen_snowflake()
                )
            else:
                val = str(self.ctx.test_user_id)
        elif "server" in name_low or "guild" in name_low:
            val = str(self.ctx.test_server_id)
        elif "channel" in name_low:
            val = str(self.ctx.test_channel_id)
        elif "rule" in name_low or "automod" in name_low:
            val = str(self.ctx.test_automod_rule_id or _gen_snowflake())
        elif "ticket" in name_low:
            val = str(self.ctx.test_ticket_id or _gen_snowflake())
        elif "token" in name_low and ("access" in path or "security" in path):
            val = str(self.ctx.test_access_token_id or _gen_snowflake())
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
            if "id" in name_low or "name" not in name_low:
                val = str(self.ctx.test_emoji_id or _gen_snowflake())
            else:
                val = "smile"
        elif "sticker" in name_low:
            val = str(self.ctx.test_sticker_id or _gen_snowflake())
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
            else:
                val = _gen_snowflake()
        return val

    def get_minimal_body(
        self, request_body: Dict[str, Any], path: str, method: str
    ) -> Dict[str, Any]:
        content = request_body.get("content", {})
        body: Any = {}
        if "application/json" in content:
            schema = content["application/json"].get("schema", {})
            body = self.generate_from_schema(schema)
        else:
            body = {}

        if isinstance(body, dict):
            test_pass = self.ctx._test_password
            assert test_pass, "self-test password must be set up before requests"
            user_config = self.ctx.config.get("test_user", {})

            if "auth/login" in path:
                body["username"] = user_config.get("username", "selftest_admin")
                body["password"] = test_pass

            if "auth/register" in path:
                body["username"] = f"user_{random.randint(10000, 99999)}"
                body["password"] = test_pass
                if "email" in body:
                    body["email"] = f"test_{random.randint(10000, 99999)}@example.com"

            if "users/@me" in path and method == "PATCH":
                if "current_password" in body:
                    body["current_password"] = test_pass
                body.pop("username", None)
                body.pop("email", None)
                body.pop("password", None)

            if "auth/2fa/enable" in path:
                if "password" in body:
                    body["password"] = test_pass

            if "users/@me/channels" in path and method == "POST":
                if self.ctx.test_other_user_id:
                    body["recipient_id"] = str(self.ctx.test_other_user_id)

            if "relationships" in path and method == "POST":
                # Send friend request TO admin user (from other_user) — setup creates other_user -> admin
                if self.ctx.test_user_id:
                    body["user_id"] = str(self.ctx.test_user_id)
                # other_session sends the request, so recipient is admin (test_user_id)
                # The sender is implicit (authenticated via other_session token)

            if "admin/security/access-tokens" in path and "/rotate" in path:
                body["token"] = secrets.token_urlsafe(48)

            if "version/negotiate" in path:
                body["client_version"] = "r.1.0-999"

            if "reports/users" in path and method == "POST":
                if self.ctx.test_other_user_id:
                    body["user_id"] = str(self.ctx.test_other_user_id)
                else:
                    body["user_id"] = "1"

            if "admin/roles" in path and (
                method == "POST" or method == "PUT" or method == "PATCH"
            ):
                if "name" in body:
                    body["name"] = "selftest_admin_role_" + secrets.token_hex(4)
                if method == "POST" and "permissions" in body:
                    body["permissions"] = {"users.read": True}

            if "admin/admin-users" in path and method == "POST":
                # create_admin_user must have a unique username/email to avoid 500
                uniq = secrets.token_hex(4)
                body["username"] = f"admintest_{uniq}"
                body["email"] = f"admintest_{uniq}@selftest.plexichat.com"
                body["password"] = test_pass
                body.pop("user_id", None)

            if "admin/admin-users" in path and method == "PUT":
                body.pop("username", None)
                body.pop("email", None)
                body.pop("password", None)

            if "admin/roles/assign" in path and method == "POST":
                body["admin_id"] = (
                    self.ctx.test_other_user_id or self.ctx.test_user_id or 1
                )
                body["role_id"] = (
                    self.ctx.test_non_system_role_id
                    or self.ctx.test_admin_role_id
                    or self.ctx._admin_role_super_id
                    or 1
                )

            if "bots/servers/" in path and "/requests/" in path and method == "PUT":
                if "approve" in body:
                    body["approve"] = True

            if "automod/rules" in path and method == "POST":
                body["rule_type"] = "keyword"
                body["config"] = {"keywords": ["test"]}
                body["server_id"] = str(self.ctx.test_server_id or 1)

            if "polls" in path and method == "POST":
                body["question"] = "Test poll question?"
                body["options"] = ["Option A", "Option B"]
                if self.ctx.test_message_id:
                    body["message_id"] = str(self.ctx.test_message_id)

            if "servers/" in path and "/channels" in path and method == "POST":
                body.pop("category_id", None)
                if self.ctx.test_server_id:
                    body.pop("server_id", None)

            if "push/tokens" in path and method == "POST":
                body["platform"] = "web"
                body["token"] = secrets.token_urlsafe(32)

            if "reports/enhanced" in path and method == "POST":
                body["priority"] = "medium"
                body["status"] = "open"

            if "scheduled-messages" in path and method == "POST":
                body["scheduled_at"] = int(time.time() * 1000) + 120000
                body["content"] = "Test scheduled message"
                body["conversation_id"] = str(
                    self.ctx.test_conversation_id or self.ctx.test_channel_id or 1
                )

            if (
                "/channels/" in path
                and "/messages" in path
                and method == "POST"
                and "search" not in path
                and "unread" not in path
            ):
                if "reply_to_id" in body:
                    if self.ctx.test_message_id:
                        body["reply_to_id"] = str(self.ctx.test_message_id)
                    else:
                        body.pop("reply_to_id", None)

            if "features/forward" in path and method == "POST":
                if self.ctx.test_message_id:
                    body["message_id"] = str(self.ctx.test_message_id)
                if self.ctx.test_conversation_id:
                    body["target_conversation_id"] = str(self.ctx.test_conversation_id)

            if "/channels/" in path and path.endswith("/threads") and method == "POST":
                body["name"] = "Self-test thread"
                body["thread_type"] = "public"
                body["auto_archive_duration"] = 1440
                body.pop("parent_message_id", None)

            if "/messages/bulk-delete" in path and method == "POST":
                # Use throwaway message IDs (not the tracked test message) so the
                # generic auto-fire exercises the endpoint with a 200 without
                # deleting resources other tests depend on. Real message deletion
                # is verified by test_bulk_delete_messages().
                if isinstance(body, dict) and "message_ids" in body:
                    body["message_ids"] = [str(self.generate_snowflake())]

            if (
                "features/voice-messages" in path
                and method == "POST"
                and "/upload" not in path
            ):
                body["conversation_id"] = str(
                    self.ctx.test_conversation_id or self.ctx.test_channel_id or 1
                )
                body["content_type"] = "audio/ogg"
                body["duration_ms"] = 5000
                body["filename"] = "voice_test.ogg"
                body["size"] = 4096
                body["url"] = "https://example.com/voice_test.ogg"

            if "name" in body:
                # Emoji/sticker paths contain "server" so they must be checked first
                if "/emojis" in path or "/stickers" in path:
                    body["name"] = "test_asset_" + secrets.token_hex(4)
                    if isinstance(body.get("name"), str):
                        body["name"] = re.sub(r"[^a-z0-9_]", "_", body["name"].lower())
                elif (
                    "server" in path
                    and "/channels" not in path
                    and "/emojis" not in path
                    and "/stickers" not in path
                    and "/roles" not in path
                ):
                    body["name"] = "Self-Test Server Update"
                elif "channel" in path:
                    body["name"] = "updated-channel"
                elif "role" in path:
                    body["name"] = "updated_role_" + secrets.token_hex(4)
                else:
                    body["name"] = "Self-Test Value"

            if (
                ("/emojis" in path or "/stickers" in path)
                and method in ("POST", "PATCH")
                and "search" not in path
            ):
                if method == "POST":
                    body["image"] = (
                        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhAFGAbKm4AAAAABJRU5ErkJggg=="
                    )
                else:
                    body.pop("image", None)
                if "server_id" in body and self.ctx.test_server_id:
                    body["server_id"] = str(self.ctx.test_server_id)

            if "stickers/" in path and "/send" in path and method == "POST":
                if "message_id" not in body or body["message_id"] == "1":
                    body["message_id"] = str(
                        self.ctx.test_message_id or self.generate_snowflake()
                    )

            if "polls/" in path and "/vote" in path and method == "POST":
                if self.ctx.test_poll_option_ids:
                    body["option_ids"] = [
                        int(oid) for oid in self.ctx.test_poll_option_ids
                    ]
                else:
                    body["option_ids"] = [self.generate_snowflake()]

            if "license" in path and method == "POST":
                try:
                    _lic_path = Path.home() / ".plexichat" / "config" / "license"
                    if _lic_path.exists():
                        _lic_b64 = base64.b64encode(_lic_path.read_bytes()).decode()
                        body["license_key"] = _lic_b64
                    else:
                        _lic_path = (
                            Path.home() / ".plexichat" / "config" / "license.json"
                        )
                        if _lic_path.exists():
                            _lic_b64 = base64.b64encode(_lic_path.read_bytes()).decode()
                            body["license_key"] = _lic_b64
                except Exception:
                    pass

            if "access-tokens" in path and "/scopes" in path and method == "POST":
                body["scope_type"] = "ip"
                body["value"] = "192.168.1.1"

            if "migrations" in path:
                if method == "GET" and re.search(r"/migrations/\d+$", path):
                    body["format"] = "001"

            if "auth/change-password" in path and method == "POST":
                test_pass = self.ctx._test_password
                assert test_pass, "self-test password must be set up before requests"
                body["current_password"] = test_pass
                body["new_password"] = test_pass

            if "features" in path and method == "PUT":
                body["rate_limit_tier"] = "standard"

            if "tier" in path and method == "PUT":
                body["tier"] = "standard"

            if "access-tokens" in path and "/rotate" in path and method == "POST":
                body["token"] = secrets.token_urlsafe(32)

            if "slowmode" in path and method == "PUT":
                body["interval_ms"] = 5000

            if "admin-users" in path and "/toggle-status" in path and method == "POST":
                if self.ctx.test_other_user_id:
                    if "user_id" in body:
                        body["user_id"] = str(self.ctx.test_other_user_id)
                # The path param itself is already resolved via get_param_value above

            if "security/lock-user" in path and method == "POST":
                body["user_id"] = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else str(self.ctx.test_user_id)
                )
                body["duration_seconds"] = None

            if "security/unlock-user" in path and method == "POST":
                body["user_id"] = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else str(self.ctx.test_user_id)
                )

            if "security/force-logout" in path and method == "POST":
                body["user_id"] = (
                    str(self.ctx.test_other_user_id)
                    if self.ctx.test_other_user_id
                    else str(self.ctx.test_user_id)
                )

            if "force-username-change" in path and method == "POST":
                # Explicitly disable username banning to avoid polluting the
                # banned_usernames table with self-test usernames.
                body["ban_current"] = False
                body["forced"] = True

            if "plexijoin/connections" in path and method == "POST":
                uniq = secrets.token_hex(4)
                body = {
                    "remote_instance_id": f"test-instance-{uniq}",
                    "remote_url": "https://test.example.com",
                    "shared_key": "test_shared_key_32_chars_long_here!",
                }

            if "bots/servers/" in path and "/approve" in path and method == "POST":
                if self.ctx.test_application_id:
                    body["application_id"] = int(self.ctx.test_application_id)

            if "bots/servers/" in path and "/requests/" in path and method == "PUT":
                if "approve" in body:
                    body["approve"] = True
                if self.ctx.test_bot_request_id:
                    body["request_id"] = str(self.ctx.test_bot_request_id)

            if "passkeys/authenticate" in path and method == "POST":
                if self.ctx.test_passkey_challenge_id and "challenge_id" in body:
                    body["challenge_id"] = self.ctx.test_passkey_challenge_id

            if "content" in body:
                body["content"] = "Self-test message content at " + time.strftime(
                    "%H:%M:%S"
                )

            if "status" in body:
                if "tickets" in path:
                    body["status"] = "open"
                elif "approval" in path:
                    body["status"] = "pending"
                elif "reports" in path:
                    body["status"] = "open"
                else:
                    body["status"] = "online"

            if "code" in body:
                body["code"] = "123456"

        return body

    def generate_from_schema(
        self, schema: Dict[str, Any], prop_name: Optional[str] = None
    ) -> Any:
        if "$ref" in schema:
            ref_path = schema["$ref"].split("/")
            ref_schema = self.ctx.openapi_spec
            for part in ref_path[1:]:
                ref_schema = ref_schema.get(part, {})
            return self.generate_from_schema(ref_schema, prop_name)

        if "allOf" in schema:
            merged = {}
            for sub in schema["allOf"]:
                res = self.generate_from_schema(sub, prop_name)
                if isinstance(res, dict):
                    merged.update(res)
            return merged

        for key in ("anyOf", "oneOf"):
            if key in schema:
                return self.generate_from_schema(schema[key][0], prop_name)

        type_ = schema.get("type")

        if not type_ and "properties" in schema:
            type_ = "object"

        if type_ == "object":
            obj = {}
            required = schema.get("required", [])
            properties = schema.get("properties", {})

            for p_name in required:
                if p_name in properties:
                    obj[p_name] = self.generate_from_schema(properties[p_name], p_name)

            for p_name, prop_schema in properties.items():
                if p_name not in obj:
                    if any(
                        x in p_name.lower()
                        for x in (
                            "id",
                            "name",
                            "type",
                            "content",
                            "username",
                            "email",
                            "password",
                            "status",
                            "category",
                        )
                    ):
                        obj[p_name] = self.generate_from_schema(prop_schema, p_name)

            if not obj and properties:
                p_name = next(iter(properties))
                obj[p_name] = self.generate_from_schema(properties[p_name], p_name)

            return obj

        elif type_ == "array":
            items = schema.get("items", {})
            return [self.generate_from_schema(items, prop_name)]

        elif type_ == "string":
            if "enum" in schema:
                return schema["enum"][0]

            if "pattern" in schema and prop_name:
                pattern = schema["pattern"]
                if pattern.startswith("^") and pattern.endswith("$"):
                    inner = pattern[1:-1]
                    alt_match = re.match(
                        r"^\(([a-zA-Z0-9_.-]+(?:\|[a-zA-Z0-9_.-]+)*)\)$", inner
                    )
                    if alt_match:
                        return alt_match.group(1).split("|")[0]
                if pattern == "^[a-zA-Z0-9_]+$":
                    return "test_value"
                if pattern == "^[a-fA-F0-9]{6}$":
                    return "ff0000"
                if pattern == "^[a-zA-Z]+$":
                    return "test"
                if pattern.startswith("^#"):
                    return "#ff0000"

            if prop_name:
                pn = prop_name.lower()
                if pn == "username":
                    return f"user_{random.randint(1000, 9999)}"
                if pn == "email":
                    return f"test_{random.randint(1000, 9999)}@example.com"
                if pn == "password":
                    return self.ctx._test_password
                if pn == "current_password":
                    return self.ctx._test_password
                if "id" in pn:
                    if "server" in pn:
                        return str(self.ctx.test_server_id or self.generate_snowflake())
                    if "channel" in pn:
                        return str(
                            self.ctx.test_channel_id or self.generate_snowflake()
                        )
                    if "message" in pn:
                        return str(
                            self.ctx.test_message_id or self.generate_snowflake()
                        )
                    if "user" in pn:
                        return str(self.ctx.test_user_id or self.generate_snowflake())
                    return str(self.generate_snowflake())
                if "content" in pn:
                    return "Test content " + secrets.token_hex(4)
                if "reason" in pn:
                    return "Self-test reason"
                if "topic" in pn:
                    return "Self-test topic"
                if "description" in pn:
                    return "Self-test description"
                if "name" in pn:
                    return "test_name_" + secrets.token_hex(4)
                if "reported_user_id" in pn:
                    return str(self.ctx.test_user_id)
                if "recipient_id" in pn:
                    return str(self.ctx.test_user_id)
                if "status" in pn:
                    return "online"
                if "category" in pn:
                    return "other"
                if "code" in pn:
                    return "123456"
                if "hash" in pn:
                    return "a" * 64
                if "version" in pn:
                    return "a.1.0-1"
                if "method" in pn:
                    return "GET"
                if "question" in pn:
                    return "Test poll question?"
                if "rule_type" in pn:
                    return "keyword"
                if "trigger_type" in pn:
                    return "keyword"
                if "action_type" in pn:
                    return "delete_message"
                if "scope_type" in pn:
                    return "ip"
                if "emoji" in pn and "id" not in pn:
                    return "smile"

            fmt = schema.get("format")
            if fmt == "email":
                return f"test_{random.randint(1000, 9999)}@example.com"
            if fmt == "password":
                return self.ctx._test_password
            if fmt == "date-time":
                return "2024-01-01T00:00:00Z"

            title = schema.get("title", "").lower()
            if "id" in title or "snowflake" in title:
                return str(self.generate_snowflake())

            return "test"

        elif type_ in ("integer", "number"):
            if prop_name:
                pn = prop_name.lower()
                if "status" in pn and "code" in pn:
                    return 200
                if "id" in pn:
                    if "server" in pn:
                        return self.ctx.test_server_id or 1
                    if "channel" in pn:
                        return self.ctx.test_channel_id or 1
                    if "message" in pn:
                        return self.ctx.test_message_id or 1
                    if "user" in pn:
                        return self.ctx.test_user_id or 1
                    if "poll" in pn:
                        return self.ctx.test_poll_id or 1
                    if "rule" in pn:
                        return self.ctx.test_automod_rule_id or 1
                    if "thread" in pn:
                        return self.ctx.test_thread_id or 1
            return 1

        elif type_ == "boolean":
            return False

        return None
