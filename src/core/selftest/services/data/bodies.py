import base64
import random
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict

from .base import DataGeneratorBase


class MinimalBodyMixin(DataGeneratorBase):
    """Build the minimal valid request body for a route."""

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

            # The admin artifact feature-settings route intentionally accepts
            # a generic Dict[str, Any], so OpenAPI cannot describe its body
            # fields and schema-driven generation produces {}.  Supply the
            # route's documented contract explicitly so the self-test exercises
            # the database write instead of failing its manual 400 validation.
            if "/admin/artifacts/features/" in path and method == "POST":
                body["server_id"] = int(self.ctx.test_server_id or 1)
                body["settings"] = {"selftest": "enabled"}

            if "auth/register" in path:
                body["username"] = f"user_{random.randint(10000, 99999)}"

            # Thread message posts require non-empty content; schema-driven
            # generation can omit it (optional in the model), so supply it
            # explicitly to exercise the real send path.
            if "/threads/" in path and path.endswith("/messages") and method == "POST":
                if not body.get("content"):
                    body["content"] = "Self-test thread message"

            # Artifact CRUD must use the real setup scope. Schema-driven
            # generation otherwise invents a conversation id, which the route
            # correctly rejects after scope validation.
            if path == "/api/v1/artifacts" and method == "POST":
                if self.ctx.test_conversation_id:
                    body["conversation_id"] = str(self.ctx.test_conversation_id)
                if self.ctx.test_channel_id:
                    body["channel_id"] = str(self.ctx.test_channel_id)
                if self.ctx.test_server_id:
                    body["server_id"] = str(self.ctx.test_server_id)
                body["title"] = "Self-test artifact"
                body["artifact_type"] = "upload"
                body["status"] = "completed"
                body["password"] = test_pass
                if "email" in body:
                    body["email"] = f"test_{random.randint(10000, 99999)}@example.com"

            # The admin storage-override route intentionally accepts a
            # free-form body, so OpenAPI-driven generation produces {}. Supply
            # a concrete quota so the self-test exercises the write path
            # instead of tripping the route's required-field guard.
            if (
                "/admin/users/" in path
                and path.endswith("/storage")
                and method == "PUT"
            ):
                body["limit_bytes"] = 10 * 1024 * 1024 * 1024

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

            if "blocked-users" in path and method == "POST":
                # Platform hierarchy refuses admin targets: point at the
                # permanently unprivileged fourth user (random ids would
                # 404 later; self would 403 here).
                _fourth = getattr(self.ctx, "test_fourth_user_id", None)
                if _fourth:
                    body["user_id"] = str(_fourth)

            if "admin/security/access-tokens" in path and "/rotate" in path:
                # Rotation values are always generated server-side (see the
                # rotate-specific rule above); never supply one.
                if isinstance(body, dict):
                    body.pop("token", None)

            if "version/negotiate" in path:
                body["client_version"] = "1.0.0.post999"

            if "reports/users" in path and method == "POST":
                if self.ctx.test_other_user_id:
                    body["user_id"] = str(self.ctx.test_other_user_id)
                else:
                    body["user_id"] = "1"

            if "reports/messages" in path and method == "POST":
                # Reported by the OTHER user (see test_endpoint session switch)
                # against the admin-owned reference message, so the route's own
                # content guard does not reject it.
                if self.ctx.test_message_id:
                    body["message_id"] = str(self.ctx.test_message_id)
                if self.ctx.test_channel_id:
                    body["channel_id"] = str(self.ctx.test_channel_id)
                if self.ctx.test_server_id:
                    body["server_id"] = str(self.ctx.test_server_id)
                body["reason"] = "Self-test message report"
                body["category"] = "spam"

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
                # Filed by the OTHER user (see test_endpoint session switch)
                # against the admin-owned reference message.
                body["priority"] = "medium"
                body["target_type"] = "message"
                if self.ctx.test_message_id:
                    body["target_id"] = str(self.ctx.test_message_id)
                if self.ctx.test_channel_id:
                    body["channel_id"] = str(self.ctx.test_channel_id)
                if self.ctx.test_server_id:
                    body["server_id"] = str(self.ctx.test_server_id)
                body["reason"] = "Self-test enhanced report"
                body["category"] = "spam"
                # Schema-driven generation invents an invalid evidence URL list;
                # clear it so the route's URL guard does not reject the request.
                body.pop("evidence_urls", None)

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

            # Both POST /vote (vote) and PUT /{id}/vote (change-vote) accept
            # the same PollVoteRequest. The generic loop exercises both
            # methods, so real option ids must be used for each — a random
            # snowflake always 400s as an invalid option.
            if "polls/" in path and "/vote" in path and method in ("POST", "PUT"):
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
                except Exception as license_error:
                    import utils.logger as logger

                    logger.debug(
                        f"selftest license fixture unavailable: {license_error}"
                    )

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

            if "users/@me/channels" in path and method == "POST":
                # DM creation needs a real, DM-accepting recipient: the
                # other test user (self-DMs are 400, random ids 404).
                if self.ctx.test_other_user_id:
                    body["recipient_id"] = str(self.ctx.test_other_user_id)

            if "features/bookmarks" in path and method == "POST":
                # Bookmarking requires access to a real message: point at
                # the seeded reference message (random ids 403 by design).
                if self.ctx.test_message_id:
                    body["message_id"] = str(self.ctx.test_message_id)
                if self.ctx.test_conversation_id:
                    body["conversation_id"] = str(self.ctx.test_conversation_id)

            if "auth/devices/trust" in path or "auth/devices/untrust" in path:
                # Trust operations only succeed for fingerprints this
                # account authenticated from; use the seeded test device.
                if getattr(self.ctx, "test_device_fingerprint", None):
                    body["fingerprint"] = self.ctx.test_device_fingerprint

            if "tier" in path and method == "PUT":
                body["tier"] = "standard"

            if "access-tokens" in path and "/rotate" in path and method == "POST":
                # Rotation values are always generated server-side;
                # caller-supplied values are rejected with 400 by design.
                # Send an empty body so the endpoint is exercised (200) and
                # the rotation itself is validated.
                body.pop("token", None)

            if "slowmode" in path and method == "PUT":
                body["interval_ms"] = 5000

            if "cross-worker-tuning" in path and method == "PATCH":
                # Runtime tuning constants are bounds-validated server-side
                # (see _TUNING_BOUNDS); send in-range values so the endpoint
                # itself (200 + audit) is exercised instead of the validator.
                body = {"glide_request_timeout_ms": 1000}

            if "soundboard" in path and path.endswith("/play") and method == "POST":
                # Playback requires a voice channel in the sound's server
                # (a text channel id 404s by design).
                if getattr(self.ctx, "test_voice_channel_id", None):
                    body["channel_id"] = str(self.ctx.test_voice_channel_id)

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

            if (
                "/servers/" in path
                and path.rstrip("/").endswith("/transfer")
                and method == "POST"
            ):
                # Ownership transfer requires the new owner to already be a
                # member: the core raises MemberNotFoundError (mapped to 404
                # "Server or member not found") otherwise. Schema-driven
                # generation invents a random snowflake for new_owner_id,
                # which deterministically 404s. Point at other_user, a real
                # member seeded by setup (fallback: the acting owner, which
                # the core accepts as a member rather than 404ing).
                if self.ctx.test_other_user_id:
                    body["new_owner_id"] = str(self.ctx.test_other_user_id)
                elif self.ctx.test_user_id:
                    body["new_owner_id"] = str(self.ctx.test_user_id)

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

            if "artifacts" in path and "convert-upload" in path:
                if "attachment_id" in body:
                    body["attachment_id"] = str(
                        self.ctx.test_attachment_id
                        if self.ctx.test_attachment_id
                        else self.generate_snowflake()
                    )
                if self.ctx.test_conversation_id:
                    body["conversation_id"] = str(self.ctx.test_conversation_id)
                if self.ctx.test_channel_id:
                    body["channel_id"] = str(self.ctx.test_channel_id)
                if self.ctx.test_server_id:
                    body["server_id"] = str(self.ctx.test_server_id)

            if "status" in body:
                if "tickets" in path:
                    body["status"] = "open"
                elif "approval" in path:
                    body["status"] = "pending"
                elif "reports" in path:
                    body["status"] = "open"
                elif "artifacts" in path:
                    body["status"] = "completed"
                else:
                    body["status"] = "online"

            if "code" in body:
                body["code"] = "123456"

        return body
