"""Core endpoint tester mixin.

Executes individual API endpoint tests with body construction,
multipart/file handling, retry logic, and side-effect capture.
"""

import time
import secrets
import re
from typing import Any, Dict

import utils.logger as logger

from .base import EndpointTesterBase


class CoreMixin(EndpointTesterBase):
    """Executes single-endpoint HTTP tests."""

    def test_endpoint(
        self,
        method: str,
        path: str,
        route_details: Dict[str, Any],
        use_other: bool = False,
    ) -> None:
        url_path = path
        query_params = {}
        active_session = self.ctx.other_session if use_other else self.ctx.session

        for param in route_details.get("parameters", []):
            p_name = param.get("name")
            val = self.ctx.data.get_param_value(p_name, path)

            logger.info(f"  Param: {p_name}={val} (in {param.get('in')})")

            if param.get("in") == "path":
                url_path = url_path.replace(f"{{{p_name}}}", val)
                url_path = url_path.replace(f"{{{p_name.lower()}}}", val)
            elif param.get("in") == "query":
                query_params[p_name] = val

        if "delay-deletion" in path and method == "POST":
            future_deletion_at = int(time.time()) + 86400
            query_params["deletion_at"] = str(future_deletion_at)

        if "/users/search" in path and method == "GET":
            query_params["username"] = self.ctx.config.get("test_user", {}).get(
                "username", "selftest_admin"
            )

        if (
            "/channels/invites/" in path or "/servers/invites/" in path
        ) and method == "POST":
            if not use_other and self.ctx.other_session:
                active_session = self.ctx.other_session

        # Relationship POST (send friend request) should use OTHER session
        # since setup sends the request from other_user -> admin user
        if (
            not use_other
            and self.ctx.other_token
            and "/relationships" in path
            and method == "POST"
        ):
            active_session = self.ctx.other_session

        json_body = None
        files = None
        form_data = {}

        request_body = route_details.get("request_body", {})
        content_types = request_body.get("content", {})

        if not content_types or (
            "multipart/form-data" not in content_types
            and "application/x-www-form-urlencoded" not in content_types
        ):
            form_params = [
                p
                for p in route_details.get("parameters", [])
                if p.get("in") in ("formData", "form")
            ]
            if form_params:
                props = {}
                for p in form_params:
                    p_schema = p.get("schema", {})
                    if not p_schema:
                        p_schema = {"type": "string"}
                    props[p["name"]] = p_schema
                content_types["multipart/form-data"] = {
                    "schema": {
                        "properties": props,
                        "required": [
                            p["name"] for p in form_params if p.get("required")
                        ],
                    }
                }

        if "multipart/form-data" in content_types:
            if "voice-messages/upload" in path:
                ogg_header = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                files = {
                    "audio": (
                        "voice_test.ogg",
                        ogg_header + b"\x00" * 1024,
                        "audio/ogg",
                    )
                }
                form_data["conversation_id"] = str(
                    self.ctx.test_conversation_id or self.ctx.test_channel_id or 1
                )
                form_data["duration_ms"] = "5000"
            else:
                if "/stickers" in path or "/emojis" in path:
                    file_field = "image"
                else:
                    file_field = "file"
                from PIL import Image
                import io as _io

                _img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                _buf = _io.BytesIO()
                _img.save(_buf, format="PNG")
                png = _buf.getvalue()
                files = {
                    file_field: (
                        "test_file.png",
                        png,
                        "image/png",
                    )
                }

                schema = content_types["multipart/form-data"].get("schema", {})
                props = schema.get("properties", {})
                for p_name, p_schema in props.items():
                    if p_name != file_field:
                        form_data[p_name] = self.ctx.data.generate_from_schema(
                            p_schema, p_name
                        )

                if "/stickers" in path or "/emojis" in path:
                    name_val = form_data.get("name", "")
                    if not name_val or len(str(name_val)) < 2:
                        form_data["name"] = "test_asset_" + secrets.token_hex(4)
                    else:
                        form_data["name"] = re.sub(
                            r"[^a-z0-9_]", "_", str(form_data["name"]).lower()
                        )
                        if len(form_data["name"]) < 2:
                            form_data["name"] = "test_asset_" + secrets.token_hex(4)
                if "/stickers/" in path and "/send" in path and method == "POST":
                    form_data["message_id"] = str(
                        self.ctx.test_message_id or self.ctx.data.generate_snowflake()
                    )
        elif "application/x-www-form-urlencoded" in content_types:
            schema = content_types["application/x-www-form-urlencoded"].get(
                "schema", {}
            )
            props = schema.get("properties", {})
            for p_name, p_schema in props.items():
                form_data[p_name] = self.ctx.data.generate_from_schema(p_schema, p_name)
            if "/stickers/" in path and "/send" in path and method == "POST":
                if "message_id" not in form_data or not form_data.get("message_id"):
                    form_data["message_id"] = str(
                        self.ctx.test_message_id or self.ctx.data.generate_snowflake()
                    )
        elif method in ("POST", "PUT", "PATCH"):
            json_body = self.ctx.data.get_minimal_body(request_body, path, method)

        if (
            json_body
            and ("/emojis" in path or "/stickers" in path)
            and "search" not in path
        ):
            if "name" not in json_body or not json_body.get("name"):
                json_body["name"] = "test_asset_" + secrets.token_hex(4)
            elif isinstance(json_body.get("name"), str):
                json_body["name"] = re.sub(
                    r"[^a-z0-9_]", "_", json_body["name"].lower()
                )

        start = time.time()
        try:
            resp = active_session.request(
                method,
                f"{self.ctx.base_url}{url_path}",
                json=json_body,
                data=form_data if form_data else None,
                files=files,
                params=query_params,
                timeout=5,
            )
            duration = (time.time() - start) * 1000

            success = 200 <= resp.status_code < 300

            traceback_data = None
            if not success and self.ctx.config.get("retry_on_failure", True):
                retry_resp = active_session.request(
                    method,
                    f"{self.ctx.base_url}{url_path}",
                    json=json_body,
                    headers={"X-Plexichat-SelfTest-Debug": "true"},
                    timeout=10,
                )
                if retry_resp.status_code >= 400:
                    try:
                        traceback_data = (
                            retry_resp.json().get("error", {}).get("traceback")
                        )
                    except Exception:
                        pass

            result = {
                "method": method,
                "path": path,
                "status_code": resp.status_code,
                "duration_ms": duration,
                "success": success,
                "traceback": traceback_data,
            }
            self.ctx.results.append(result)

            # If the auto-loop just tested POST /admin/blocked-users and
            # blocked the admin user, immediately unblock so subsequent
            # upload tests in the same loop don't fail.
            if (
                success
                and method == "POST"
                and "/blocked-users" in path
                and "/admin/" in path
                and self.ctx.test_user_id
            ):
                try:
                    unblock_url = f"{self.ctx.base_url}/api/v1/admin/blocked-users/{self.ctx.test_user_id}"
                    uresp = active_session.delete(unblock_url, timeout=5)
                    if uresp.status_code in (200, 204):
                        logger.info(
                            "Auto-unblocked test user from uploads after "
                            "POST /admin/blocked-users"
                        )
                except Exception as exc:
                    logger.debug(f"Auto-unblock failed (non-fatal): {exc}")

            # Capture webhook ID from auto-loop POST /webhooks so subsequent
            # PATCH /webhooks/{id} and POST execute tests use the right ID.
            # Always capture (even if setup left a stale value) so the latest
            # webhook ID/token are used throughout the test suite.
            if (
                success
                and method == "POST"
                and "/webhooks" in path
                and "/webhook." not in path
            ):
                try:
                    webhook_data = resp.json()
                    if isinstance(webhook_data, dict):
                        wid = webhook_data.get("id")
                        if wid:
                            self.ctx.test_webhook_id = int(wid)
                            self.ctx.test_webhook_token = webhook_data.get("token")
                            logger.debug(
                                f"Captured webhook id={wid} from auto-loop POST"
                            )
                except Exception:
                    pass

            if not success:
                logger.error(
                    f"FAILED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )
                if resp.status_code == 400:
                    logger.error(f"  Validation Error: {resp.text[:200]}")
                if traceback_data:
                    logger.error(f"Captured Traceback for {path}:\n{traceback_data}")
            elif self.ctx.config.get("verbose", False):
                logger.info(
                    f"PASSED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )

        except Exception as e:
            self.ctx.results.append(
                {
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "error": str(e),
                }
            )
            logger.error(f"EXCEPTION: {method:<6} {path:<40} -> {e}")

    def test_bulk_delete_messages(self) -> None:
        """Verify POST /channels/{id}/messages/bulk-delete end-to-end.

        Creates a few real messages in the test channel, bulk-deletes them,
        and confirms they are gone. This genuinely exercises the feature that
        emits MESSAGE_DELETE_BULK to connected clients.
        """
        channel_id = self.ctx.test_channel_id
        if not channel_id:
            logger.warning("test_bulk_delete_messages skipped: no test channel")
            return

        logger.info(
            f"Testing POST /api/v1/channels/{channel_id}/messages/bulk-delete "
            "(creating fresh messages)..."
        )
        created_ids = []
        for i in range(3):
            try:
                resp = self.ctx.session.post(
                    f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages",
                    json={"content": f"bulk-delete selftest {i}"},
                    timeout=5,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    mid = data.get("id") or (data.get("message") or {}).get("id")
                    if mid:
                        created_ids.append(str(mid))
            except Exception as exc:
                logger.debug(f"bulk-delete message creation attempt {i} failed: {exc}")

        logger.info(
            f"Created {len(created_ids)} messages for bulk-delete: {created_ids}"
        )

        if not created_ids:
            logger.error(
                "FAILED: test_bulk_delete_messages -> could not create test messages"
            )
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": f"/api/v1/channels/{channel_id}/messages/bulk-delete",
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "error": "message creation failed",
                }
            )
            return

        bulk_resp = self.ctx.session.post(
            f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/bulk-delete",
            json={"message_ids": created_ids},
            timeout=5,
        )
        success = 200 <= bulk_resp.status_code < 300
        self.ctx.results.append(
            {
                "method": "POST",
                "path": f"/api/v1/channels/{channel_id}/messages/bulk-delete",
                "status_code": bulk_resp.status_code,
                "duration_ms": 0,
                "success": success,
                "traceback": None,
            }
        )
        if not success:
            logger.error(
                f"FAILED: test_bulk_delete_messages -> Status {bulk_resp.status_code} "
                f"({bulk_resp.text[:200]})"
            )
            return

        logger.info(
            f"Bulk-delete PASSED -> {bulk_resp.status_code} "
            f"({len(created_ids)} messages deleted); verifying removal..."
        )

        # Confirm the messages are actually gone.
        all_gone = True
        for mid in created_ids:
            try:
                check = self.ctx.session.get(
                    f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/{mid}",
                    timeout=5,
                )
                if check.status_code == 200:
                    all_gone = False
                    logger.error(
                        f"FAILED: test_bulk_delete_messages -> message {mid} still present"
                    )
                    self.ctx.results.append(
                        {
                            "method": "GET",
                            "path": f"/api/v1/channels/{channel_id}/messages/{mid}",
                            "status_code": check.status_code,
                            "duration_ms": 0,
                            "success": False,
                            "error": "message not deleted",
                        }
                    )
            except Exception as exc:
                logger.debug(f"bulk-delete verification for {mid} errored: {exc}")

        if all_gone:
            logger.info("Bulk-delete verification PASSED -> all messages removed")
