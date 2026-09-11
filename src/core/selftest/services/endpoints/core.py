"""Core endpoint tester mixin.

Executes individual API endpoint tests with body construction,
multipart/file handling, retry logic, and side-effect capture.
"""

import time
import secrets
import re
from typing import Any, Dict

import utils.logger as logger

from ...db_guard import sanitize_error
from .base import EndpointTesterBase

# Retrying a non-idempotent request re-executes its side effects
# (duplicate resources, duplicate sends). Only these methods may be
# retried freely; POST/PATCH require an explicit opt-in plus an
# Idempotency-Key header so the server can deduplicate.
_IDEMPOTENT_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})


class CoreMixin(EndpointTesterBase):
    """Executes single-endpoint HTTP tests."""

    def test_endpoint(
        self,
        method: str,
        path: str,
        route_details: Dict[str, Any],
        use_other: bool = False,
        session: Any = None,
    ) -> None:
        url_path = path
        query_params = {}
        active_session = (
            session
            if session is not None
            else (self.ctx.other_session if use_other else self.ctx.session)
        )

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
            session is None
            and ("/channels/invites/" in path or "/servers/invites/" in path)
            and method == "POST"
        ):
            if not use_other and self.ctx.other_session:
                active_session = self.ctx.other_session

        # Artifact export endpoint requires a valid export_format query param
        if "/artifacts/" in path and path.endswith("/export"):
            query_params["export_format"] = "html"

        # Relationship POST (send friend request) should use OTHER session
        # since setup sends the request from other_user -> admin user
        if (
            session is None
            and not use_other
            and self.ctx.other_token
            and "/relationships" in path
            and method == "POST"
        ):
            active_session = self.ctx.other_session

        # Report endpoints must be filed by the OTHER user against content the
        # admin owns (e.g. the reference message), otherwise the route's
        # "cannot report your own content" guard rejects the auto-loop request
        # with a 400. The OTHER user is a server member with read access to the
        # reference message, so the report is valid.
        if (
            session is None
            and not use_other
            and self.ctx.other_token
            and ("/reports/messages" in path or "/reports/enhanced" in path)
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
                if "/soundboard" in path and method == "POST":
                    # Sound upload takes multipart ``file`` (audio) + ``name``.
                    # Soundboard accepts MP3/OGG only: synthesize a minimal
                    # OGG-capsule (OggS magic) like the voice-message probe.
                    file_field = "file"
                    _ogg = (
                        b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
                        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 512
                    )
                    files = {
                        "file": (
                            "test_sound.ogg",
                            _ogg,
                            "audio/ogg",
                        )
                    }
                elif "/stickers" in path or "/emojis" in path:
                    file_field = "image"
                else:
                    file_field = "file"
                if files is None:
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

                if (
                    "/stickers" in path
                    or "/emojis" in path
                    or ("/soundboard" in path and method == "POST")
                ):
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
            and ("/emojis" in path or "/stickers" in path or "/soundboard" in path)
            and "search" not in path
        ):
            if "name" not in json_body or not json_body.get("name"):
                json_body["name"] = "test_asset_" + secrets.token_hex(4)
            elif isinstance(json_body.get("name"), str):
                json_body["name"] = re.sub(
                    r"[^a-z0-9_]", "_", json_body["name"].lower()
                )

        # Ownership transfer requires the new owner (other_user, set by
        # get_minimal_body) to be a current member — the route 404s by
        # design otherwise. Setup seeds other_user as a member, but an
        # earlier ban/kick/leave probe may have stranded them (a failed
        # probe skips the auto-restore below), so best-effort rejoin
        # through a fresh invite before exercising the endpoint.
        if (
            method == "POST"
            and "/servers/" in path
            and path.rstrip("/").endswith("/transfer")
            and self.ctx.test_other_user_id
            and self.ctx.test_channel_id
        ):
            try:
                _tinv = self.ctx.session.post(
                    f"{self.ctx.base_url}/api/v1/channels/"
                    f"{self.ctx.test_channel_id}/invites",
                    json={"max_uses": 5},
                    timeout=5,
                )
                if 200 <= _tinv.status_code < 300:
                    _tcode = _tinv.json().get("code")
                    if _tcode and self.ctx.other_session is not None:
                        self.ctx.other_session.post(
                            f"{self.ctx.base_url}/api/v1/channels/invites/{_tcode}",
                            timeout=10,
                        )
            except Exception as exc:
                logger.debug(f"Transfer membership-ensure note: {exc}")

        start = time.time()
        try:
            # The media proxy fetches remote content server-side; its
            # latency is bounded by the origin, not the app. Give that one
            # path room (it still fails loudly past the deadline).
            req_timeout = 30 if url_path.startswith("/api/v1/media/proxy") else 5
            # M9: POST /auth/refresh rotates the bearer and the rotated
            # credential leaves the JSON body ONLY for explicit non-browser
            # opt-in. This probe adopts the rotated token from the body
            # below, so drive the Bearer path here (same opt-in the login
            # setup already uses); otherwise the runner keeps the revoked
            # bearer and every later auto-loop request 401s.
            request_headers: Dict[str, str] = {}
            if method == "POST" and url_path.endswith("/auth/refresh"):
                request_headers["X-Plexichat-Token-Mode"] = "bearer"
            resp = active_session.request(
                method,
                f"{self.ctx.base_url}{url_path}",
                json=json_body,
                data=form_data if form_data else None,
                files=files,
                params=query_params,
                headers=request_headers or None,
                timeout=req_timeout,
            )
            duration = (time.time() - start) * 1000

            success = 200 <= resp.status_code < 300

            # Honest 503 for optional deps (e.g. /qr without qrcode lib):
            # record as SKIPPED with reason, never as pass or as
            # accepted-500. Any 500 here still fails loudly below.
            is_optional_unavailable = resp.status_code == 503 and (
                url_path.startswith("/api/v1/qr") or url_path.startswith("/qr")
            )

            traceback_data = None
            if not success and self.ctx.config.get("retry_on_failure", True):
                # Preserve the original request shape on retry.  The previous
                # retry dropped form fields, multipart files, and query params,
                # so a retry could report a different failure (or mutate a
                # resource with an incomplete body) than the request being
                # diagnosed.
                non_idempotent = method.upper() not in _IDEMPOTENT_RETRY_METHODS
                if non_idempotent and not self.ctx.config.get(
                    "retry_non_idempotent", False
                ):
                    logger.debug(
                        f"Skipping retry of side-effecting {method} {path} "
                        "(non-idempotent; set selftest.retry_non_idempotent "
                        "against servers that honour Idempotency-Key)"
                    )
                else:
                    extra_headers: Dict[str, str] = {
                        "X-Plexichat-SelfTest-Debug": "true",
                        # Preserve the M9 bearer opt-in so the retry
                        # exercises the same credential path as the attempt.
                        **request_headers,
                    }
                    if non_idempotent:
                        # Same key for the original attempt and the retry so
                        # a compliant server deduplicates the side effect.
                        idempotency_key = secrets.token_hex(16)
                        extra_headers["Idempotency-Key"] = idempotency_key
                    retry_resp = active_session.request(
                        method,
                        f"{self.ctx.base_url}{url_path}",
                        json=json_body,
                        data=form_data if form_data else None,
                        files=files,
                        params=query_params,
                        headers=extra_headers,
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
            if is_optional_unavailable:
                result["success"] = False
                result["skipped"] = True
                result["note"] = (
                    f"optional dependency unavailable (503): {method} {url_path}"
                )
                logger.info(
                    f"SKIPPED (optional dep unavailable): {method} {path} -> 503"
                )
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

            # Ban/kick tests remove other_user's server membership; an
            # unban does NOT restore it. Restore other immediately after a
            # successful ban (unban + rejoin), unban, or kick so later
            # membership-dependent tests (thread add/join, channel views)
            # exercise a clean state instead of a stranded non-member.
            if success and method in ("DELETE", "POST", "PUT"):
                try:
                    _oid = str(self.ctx.test_other_user_id or "")
                    _is_ban = (
                        method == "PUT"
                        and "/bans/" in path
                        and _oid
                        and path.rstrip("/").endswith(f"/{_oid}")
                    )
                    _is_unban = (
                        method == "DELETE"
                        and "/bans/" in path
                        and _oid
                        and path.rstrip("/").endswith(f"/{_oid}")
                    )
                    _is_kick = "/kick" in path and _oid and _oid in path
                    if _is_ban:
                        # Unban right away: the ban endpoint itself is fully
                        # exercised by the 200 above; leaving the ban in
                        # place would strand every later membership test.
                        try:
                            _srv = path.split("/bans/")[0].rstrip("/").split("/")[-1]
                            active_session.delete(
                                f"{self.ctx.base_url}/api/v1/servers/"
                                f"{_srv}/bans/{_oid}",
                                timeout=5,
                            )
                        except Exception:
                            pass
                    if (_is_ban or _is_unban or _is_kick) and self.ctx.test_channel_id:
                        _inv = active_session.post(
                            f"{self.ctx.base_url}/api/v1/channels/"
                            f"{self.ctx.test_channel_id}/invites",
                            json={"max_uses": 5},
                            timeout=5,
                        )
                        if 200 <= _inv.status_code < 300:
                            _code = _inv.json().get("code")
                            if _code and self.ctx.other_session is not None:
                                _rj = self.ctx.other_session.post(
                                    f"{self.ctx.base_url}/api/v1/channels/"
                                    f"invites/{_code}",
                                    timeout=10,
                                )
                                if 200 <= _rj.status_code < 300:
                                    logger.info(
                                        "Rejoined other_user after ban/kick probe"
                                    )
                except Exception as exc:
                    logger.debug(f"Membership restore note: {exc}")
            # The leave test makes other_user leave the server by design;
            # rejoin immediately afterwards so later membership-dependent
            # tests (thread add/join, channel views) exercise a clean
            # state instead of a stranded non-member.
            if (
                success
                and method == "POST"
                and "/leave" in path
                and "/servers/" in path
                and active_session is self.ctx.other_session
                and self.ctx.test_channel_id
            ):
                try:
                    _linv = self.ctx.session.post(
                        f"{self.ctx.base_url}/api/v1/channels/"
                        f"{self.ctx.test_channel_id}/invites",
                        json={"max_uses": 5},
                        timeout=5,
                    )
                    if 200 <= _linv.status_code < 300:
                        _lcode = _linv.json().get("code")
                        if _lcode and self.ctx.other_session is not None:
                            _lrj = self.ctx.other_session.post(
                                f"{self.ctx.base_url}/api/v1/channels/invites/{_lcode}",
                                timeout=10,
                            )
                            if 200 <= _lrj.status_code < 300:
                                logger.info("Rejoined other_user after leave probe")
                except Exception as exc:
                    logger.debug(f"Leave-restore note: {exc}")
            # Ownership transfer moves the server to other_user by design;
            # hand it back to the admin right away so later owner-only
            # probes (PATCH/DELETE server) exercise the intended path
            # instead of 403ing against the new owner.
            if (
                success
                and method == "POST"
                and "/servers/" in path
                and path.rstrip("/").endswith("/transfer")
                and active_session is self.ctx.session
                and self.ctx.other_session is not None
                and self.ctx.test_user_id
            ):
                try:
                    _back = self.ctx.other_session.post(
                        f"{self.ctx.base_url}{url_path}",
                        json={"new_owner_id": str(self.ctx.test_user_id)},
                        timeout=10,
                    )
                    if 200 <= _back.status_code < 300:
                        logger.info("Transferred server back to admin after probe")
                    else:
                        logger.warning(
                            f"Transfer-back probe returned {_back.status_code}"
                        )
                except Exception as exc:
                    logger.debug(f"Transfer-back note: {exc}")
            # Relationship blocks are privacy-enforcing (see above): unblock
            # the same target right away so later social flows stay clean.
            if success and method == "POST" and path.endswith("/relationships/block"):
                try:
                    _target = str(
                        (json_body or {}).get("user_id")
                        or (json_body or {}).get("blocked_id")
                        or ""
                    )
                    if _target and _target.isdigit():
                        _unblock_resp = active_session.delete(
                            f"{self.ctx.base_url}/api/v1/relationships/{_target}",
                            timeout=5,
                        )
                        if 200 <= _unblock_resp.status_code < 300:
                            logger.info(
                                "Auto-unblocked relationship target after "
                                "POST /relationships/block"
                            )
                        if (
                            _target
                            == str(
                                self.ctx.test_other_user_id
                                if self.ctx.test_other_user_id
                                else ""
                            )
                            and active_session is self.ctx.session
                            and self.ctx.other_session is not None
                        ):
                            try:
                                self.ctx.other_session.post(
                                    f"{self.ctx.base_url}/api/v1/relationships",
                                    json={
                                        "user_id": self.ctx.test_user_id,
                                        "message": "Self-test friend request",
                                    },
                                    timeout=10,
                                )
                                logger.debug(
                                    "Re-sent seeded friend request after unblock"
                                )
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug(f"Relationship auto-unblock note: {exc}")

            # Sound permission probes can leave deny rows behind, which
            # would 403 the later playback test. Reset the same role to
            # allow right away: the permission endpoint itself is fully
            # exercised by the 200 above.
            if (
                success
                and method == "PUT"
                and "/soundboard/" in path
                and "/permissions/" in path
            ):
                try:
                    import re as _re2

                    _m = _re2.search(
                        r"/api/v1/servers/(\d+)/soundboard/(\d+)/permissions/(\d+)",
                        path,
                    )
                    if _m:
                        _srv, _snd, _rid = _m.groups()
                        active_session.put(
                            f"{self.ctx.base_url}/api/v1/servers/{_srv}/"
                            f"soundboard/{_snd}/permissions/{_rid}",
                            json={"can_use": True},
                            timeout=5,
                        )
                        logger.debug("Reset soundboard permission to allow after probe")
                except Exception as exc:
                    logger.debug(f"Sound permission reset note: {exc}")
            # Token rotation (F3): POST /auth/refresh mints a fresh secret
            # and invalidates the old bearer immediately. A real client must
            # adopt the rotated token atomically, so the runner does the same
            # here instead of excluding the endpoint.
            if success and method == "POST" and url_path.endswith("/auth/refresh"):
                try:
                    _refresh_json = resp.json()
                    _new_token = None
                    if isinstance(_refresh_json, dict):
                        _new_token = _refresh_json.get("token") or _refresh_json.get(
                            "access_token"
                        )
                    if _new_token:
                        active_session.headers.update(
                            {"Authorization": f"Bearer {_new_token}"}
                        )
                        if active_session is self.ctx.session:
                            self.ctx.token = _new_token
                        elif active_session is self.ctx.other_session:
                            self.ctx.other_token = _new_token
                        logger.debug("Adopted rotated token after /auth/refresh")
                except Exception as _refresh_e:
                    logger.debug(f"Refresh token adoption note: {_refresh_e}")

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

            # Capture soundboard sound ID from auto-loop upload so subsequent
            # GET/PATCH/PUT/play tests address a real sound instead of a
            # fuzzed id (which 404s by design).
            if success and method == "POST" and path.endswith("/soundboard"):
                try:
                    sound_data = resp.json()
                    if isinstance(sound_data, dict):
                        sid = sound_data.get("id")
                        if sid:
                            self.ctx.test_soundboard_id = int(sid)
                            logger.debug(f"Captured soundboard sound id={sid}")
                except Exception:
                    pass

            # Capture artifact ID from auto-loop POST /artifacts so subsequent
            # GET/PATCH/DELETE /artifacts/{id} tests use a real, existing id.
            if success and method == "POST" and path == "/api/v1/artifacts":
                try:
                    artifact_data = resp.json()
                    if isinstance(artifact_data, dict):
                        aid = artifact_data.get("id")
                        if aid:
                            self.ctx.test_artifact_id = int(aid)
                            logger.debug(
                                f"Captured artifact id={aid} from auto-loop POST"
                            )
                except Exception:
                    pass

            if not success:
                logger.error(
                    f"FAILED: {method:<6} {path:<40} -> Status {resp.status_code} ({duration:.1f}ms)"
                )
                if resp.status_code in (400, 401, 403):
                    try:
                        logger.error(f"  Body: {resp.text[:300]}")
                    except Exception:
                        pass
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
                    # Sanitized: raw exception strings must not reach shared
                    # result state that status endpoints can expose.
                    "error": sanitize_error(e),
                }
            )
            logger.error(f"EXCEPTION: {method:<6} {path:<40} -> {sanitize_error(e)}")

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

        # Confirm the messages render as deleted tombstones (deleted flag
        # set, content blank) rather than disappearing entirely.
        all_gone = True
        for mid in created_ids:
            try:
                check = self.ctx.session.get(
                    f"{self.ctx.base_url}/api/v1/channels/{channel_id}/messages/{mid}",
                    timeout=5,
                )
                if check.status_code != 200:
                    all_gone = False
                    logger.error(
                        f"FAILED: test_bulk_delete_messages -> message {mid} missing (expected tombstone)"
                    )
                else:
                    data = check.json()
                    if not data.get("deleted") or data.get("content") not in (None, ""):
                        all_gone = False
                        logger.error(
                            f"FAILED: test_bulk_delete_messages -> message {mid} not marked deleted"
                        )
                    if not all_gone:
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
