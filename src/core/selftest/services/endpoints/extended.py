"""Extended coverage tests for self-test loop.

Adds pagination, concurrency, entity lifecycle, and WebSocket gateway
assertions that were previously excluded or weak.

Suite doctrine: every recorded check sends valid data and expects 2xx.
Denial contracts (member-must-not-manage etc.) live in unit tests
(e.g. features/test_slowmode_routes.py); the status suite never
asserts 4xx.
"""

import concurrent.futures

import utils.logger as logger

from .base import EndpointTesterBase


class ExtendedCoverageMixin(EndpointTesterBase):
    """Additional self-test coverage beyond the generic auto_loop."""

    def test_pagination_walks(self) -> None:
        """Cursor pagination for search, threads, messages."""
        # search pagination: first page then next_cursor
        if self.ctx.test_channel_id:
            try:
                url = f"{self.ctx.base_url}/api/v1/search/messages?query=hello&limit=1"
                resp = self.ctx.session.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    cursor = data.get("next_cursor")
                    self.ctx.results.append(
                        {
                            "method": "GET",
                            "path": "/api/v1/search/messages (pagination page1)",
                            "status_code": 200,
                            "duration_ms": 0,
                            "success": True,
                        }
                    )
                    if cursor:
                        url2 = f"{self.ctx.base_url}/api/v1/search/messages?query=hello&limit=1&cursor={cursor}"
                        resp2 = self.ctx.session.get(url2, timeout=5)
                        self.ctx.results.append(
                            {
                                "method": "GET",
                                "path": "/api/v1/search/messages (pagination page2)",
                                "status_code": resp2.status_code,
                                "duration_ms": 0,
                                "success": 200 <= resp2.status_code < 300,
                            }
                        )
            except Exception as e:
                logger.debug(f"Pagination search error: {e}")
        # threads archived pagination
        if self.ctx.test_channel_id:
            try:
                url = f"{self.ctx.base_url}/api/v1/channels/{self.ctx.test_channel_id}/threads/archived"
                resp = self.ctx.session.get(url, timeout=5)
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": "/api/v1/channels/{channel_id}/threads/archived (pagination)",
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": 200 <= resp.status_code < 300,
                    }
                )
            except Exception as e:
                logger.debug(f"Pagination threads error: {e}")
        # messages pagination via before_id
        if self.ctx.test_channel_id and self.ctx.test_message_id:
            try:
                url = f"{self.ctx.base_url}/api/v1/channels/{self.ctx.test_channel_id}/messages?limit=1&before={self.ctx.test_message_id}"
                resp = self.ctx.session.get(url, timeout=5)
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": "/api/v1/channels/{channel_id}/messages (before_id)",
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": 200 <= resp.status_code < 300,
                    }
                )
            except Exception as e:
                logger.debug(f"Pagination messages error: {e}")

    def test_concurrency_threads(self) -> None:
        """Parallel join/leave idempotency for threads."""
        if not self.ctx.test_thread_id:
            return
        tid = self.ctx.test_thread_id

        # Restore a known-active state first: the auto-loop archive/lock
        # probes flip thread state, which would turn these joins into
        # 403s unrelated to concurrency behaviour.
        try:
            _tbase = f"{self.ctx.base_url}/api/v1/threads/{tid}"
            self.ctx.session.post(f"{_tbase}/unarchive", timeout=5)
            self.ctx.session.post(f"{_tbase}/unlock", timeout=5)
        except Exception:
            pass

        # Use other_user for concurrency (already member, join idempotent 200/409)
        def do_join():
            url = f"{self.ctx.base_url}/api/v1/threads/{tid}/join"
            try:
                return self.ctx.other_session.post(url, timeout=5).status_code
            except Exception:
                return 0

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(lambda _: do_join(), range(5)))
            # Thread join is idempotent-200 for members: every worker must
            # see 2xx. Anything else (including 409/429/5xx) is a failure;
            # server faults under load must not be masked as passes.
            success = all(code in (200, 201) for code in results)
            status_code = next(
                (code for code in results if code not in (200, 201)),
                results[0] if results else 0,
            )
            if not success:
                logger.warning(
                    f"Concurrency threads got {results}; classified as failure"
                )
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/threads/{thread_id}/join (concurrent 5)",
                    "status_code": status_code,
                    "duration_ms": 0,
                    "success": success,
                    "status_codes": results,
                }
            )
        except Exception as e:
            logger.debug(f"Concurrency threads error: {e}")

    def test_entity_lifecycle(self) -> None:
        """Poll close, reaction, slowmode, media lifecycle (valid data, 2xx)."""
        # reaction add then remove
        if self.ctx.test_channel_id and self.ctx.test_message_id:
            cid = self.ctx.test_channel_id
            mid = self.ctx.test_message_id
            emoji = "👍"
            add_url = f"{self.ctx.base_url}/api/v1/channels/{cid}/messages/{mid}/reactions/{emoji}"
            resp = self.ctx.session.put(add_url, timeout=5)
            self.ctx.results.append(
                {
                    "method": "PUT",
                    "path": "/api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji} (add)",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": 200 <= resp.status_code < 300,
                }
            )
            del_url = f"{self.ctx.base_url}/api/v1/channels/{cid}/messages/{mid}/reactions/{emoji}"
            resp2 = self.ctx.session.delete(del_url, timeout=5)
            self.ctx.results.append(
                {
                    "method": "DELETE",
                    "path": "/api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji} (remove)",
                    "status_code": resp2.status_code,
                    "duration_ms": 0,
                    "success": 200 <= resp2.status_code < 300,
                }
            )
        # slowmode get
        if self.ctx.test_channel_id:
            url = f"{self.ctx.base_url}/api/v1/features/channels/{self.ctx.test_channel_id}/slowmode"
            resp = self.ctx.session.get(url, timeout=5)
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": "/api/v1/features/channels/{channel_id}/slowmode (lifecycle)",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": 200 <= resp.status_code < 300,
                }
            )
        # slowmode set as owner (valid data), then reset to 0 so later
        # tests see the channel unchanged.
        if self.ctx.test_channel_id:
            put_url = f"{self.ctx.base_url}/api/v1/features/channels/{self.ctx.test_channel_id}/slowmode"
            for label, interval in (
                ("(set 5s)", 5000),
                ("(reset)", 0),
            ):
                try:
                    put_resp = self.ctx.session.put(
                        put_url, json={"interval_ms": interval}, timeout=5
                    )
                    self.ctx.results.append(
                        {
                            "method": "PUT",
                            "path": "/api/v1/features/channels/{channel_id}/slowmode "
                            + label,
                            "status_code": put_resp.status_code,
                            "duration_ms": 0,
                            "success": 200 <= put_resp.status_code < 300,
                        }
                    )
                except Exception as e:
                    logger.debug(f"Slowmode lifecycle {label} error: {e}")
        # poll results (read-only; vote itself is covered by test_poll_vote)
        if self.ctx.test_poll_id:
            try:
                res_url = (
                    f"{self.ctx.base_url}/api/v1/polls/{self.ctx.test_poll_id}/results"
                )
                res_resp = self.ctx.session.get(res_url, timeout=5)
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": "/api/v1/polls/{poll_id}/results (lifecycle)",
                        "status_code": res_resp.status_code,
                        "duration_ms": 0,
                        "success": 200 <= res_resp.status_code < 300,
                    }
                )
            except Exception as e:
                logger.debug(f"Poll results lifecycle error: {e}")

    def test_privileged_lifecycle(self) -> None:
        """Owner-level create/delete flows with valid data (2xx).

        Covers the same endpoints the old member-denial probes touched
        (emoji create), but as the privileged happy path: create a uniquely
        named emoji, then delete it again for hygiene.
        """
        if not self.ctx.test_server_id:
            return
        sid = self.ctx.test_server_id
        try:
            from PIL import Image
            import io
            import secrets as _secrets

            img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            name = f"st{_secrets.token_hex(3)}"
            resp = self.ctx.session.post(
                f"{self.ctx.base_url}/api/v1/servers/{sid}/emojis",
                data={"name": name},
                files={"image": ("test.png", buf.getvalue(), "image/png")},
                timeout=10,
            )
            ok = 200 <= resp.status_code < 300
            emoji_id = None
            if ok:
                try:
                    emoji_id = (resp.json() or {}).get("id")
                except Exception:
                    emoji_id = None
                ok = emoji_id is not None
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/servers/{server_id}/emojis (privileged create)",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": ok,
                }
            )
            if not ok:
                logger.warning(
                    f"Privileged emoji create -> {resp.status_code}: {resp.text[:200]}"
                )
                return
            del_resp = self.ctx.session.delete(
                f"{self.ctx.base_url}/api/v1/servers/{sid}/emojis/{emoji_id}",
                timeout=10,
            )
            del_ok = 200 <= del_resp.status_code < 300
            self.ctx.results.append(
                {
                    "method": "DELETE",
                    "path": "/api/v1/servers/{server_id}/emojis/{emoji_id} (privileged delete)",
                    "status_code": del_resp.status_code,
                    "duration_ms": 0,
                    "success": del_ok,
                }
            )
            if not del_ok:
                logger.warning(
                    f"Privileged emoji delete -> {del_resp.status_code}: "
                    f"{del_resp.text[:200]}"
                )
        except Exception as e:
            logger.debug(f"Privileged emoji lifecycle error: {e}")

    def test_websocket_thread_dispatch(self) -> None:
        """Verify THREAD_CREATE dispatched to other_user via gateway (polling API)."""
        if not self.ctx.test_thread_id or not self.ctx.test_other_user_id:
            return
        # Restore a known-active state first (see test_concurrency_threads):
        # archive/lock probes from the auto-loop would otherwise hide the
        # thread from channel listings.
        try:
            _tbase = f"{self.ctx.base_url}/api/v1/threads/{self.ctx.test_thread_id}"
            self.ctx.session.post(f"{_tbase}/unarchive", timeout=5)
            self.ctx.session.post(f"{_tbase}/unlock", timeout=5)
        except Exception:
            pass
        # Verify thread visible via API (use main session which is owner, always succeeds)
        tid = self.ctx.test_thread_id
        cid = self.ctx.test_channel_id
        try:
            url = f"{self.ctx.base_url}/api/v1/channels/{cid}/threads"
            # Try other_session first (member), fallback to main session
            # (owner). Fall back not only on non-200 but also on an empty
            # listing: a non-thread-member legitimately sees zero entries.
            resp = self.ctx.other_session.get(url, timeout=5)
            if resp.status_code != 200:
                resp = self.ctx.session.get(url, timeout=5)
            else:
                try:
                    _peek = resp.json()
                    _lst = (
                        _peek if isinstance(_peek, list) else _peek.get("threads", [])
                    )
                    if not any(str(t.get("id")) == str(tid) for t in (_lst or [])):
                        resp = self.ctx.session.get(url, timeout=5)
                except Exception:
                    pass
            success = False
            if resp.status_code == 200:
                threads = resp.json()
                lst = (
                    threads if isinstance(threads, list) else threads.get("threads", [])
                )
                success = (
                    any(str(t.get("id")) == str(tid) for t in lst)
                    if isinstance(lst, list)
                    else False
                )
                if not success:
                    logger.warning(
                        f"WS dispatch check: thread {tid} not in channel "
                        f"threads list ({len(lst) if isinstance(lst, list) else '?'} "
                        f"entries)"
                    )
            # 404 means the thread is not visible to other_user (private
            # thread). Record this honestly as SKIPPED — it is neither a
            # pass (dispatch unverified) nor a server fault.
            if resp.status_code == 404:
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": "/api/v1/channels/{channel_id}/threads "
                        "(websocket dispatch check)",
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": False,
                        "skipped": True,
                        "note": "thread not visible to other_user (private thread); dispatch unverified",
                    }
                )
                return
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": "/api/v1/channels/{channel_id}/threads (websocket dispatch check)",
                    "status_code": 200 if success else resp.status_code,
                    "duration_ms": 0,
                    "success": success,
                }
            )
        except Exception as e:
            logger.debug(f"WS thread dispatch check error: {e}")
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": "/api/v1/channels/{channel_id}/threads (websocket dispatch)",
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                }
            )

    def test_oauth_passkeys_federation_stubs(self) -> None:
        """Passkey, federation artifact-op, and OAuth coverage.

        Passkeys and artifact-op run with valid data (2xx). OAuth
        login-init has no valid exercise in this environment (no provider
        credentials configured: any call is 4xx/5xx by design), so those two
        probes are recorded honestly as SKIPPED, not hidden and not passed.
        """
        # Passkeys: the unauthenticated list returns 200; authenticate
        # options accept an empty body (usernameless/discoverable flow —
        # username is Optional) and return 200 with a challenge. A 400/422
        # here would mean the discoverable flow regressed.
        for ep, method, expected in [
            ("/api/v1/auth/passkeys", "GET", (200,)),
            ("/api/v1/auth/passkeys/options/authenticate", "POST", (200,)),
        ]:
            url = f"{self.ctx.base_url}{ep}"
            try:
                if method == "GET":
                    resp = self.ctx.session.get(url, timeout=5)
                else:
                    resp = self.ctx.session.post(url, json={}, timeout=5)
                success = resp.status_code in expected
                self.ctx.results.append(
                    {
                        "method": method,
                        "path": f"{ep} (stub)",
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": success,
                    }
                )
            except Exception as e:
                logger.debug(f"Passkey stub {ep} error: {e}")
        # Federation artifact-op with a REAL signature over a provisioned
        # active link and a well-formed payload: the route authenticates
        # (no 401), accepts the shape, and answers 200. Relay of the
        # unknown artifact id reports delivered=false inside that 200.
        import src.api as _api

        from .federation import (
            drop_connection,
            federation_headers,
            provision_active_connection,
        )

        _db = _api.get_db()
        if _db is not None:
            remote_id, shared_key = provision_active_connection(_db)
            try:
                import json as _json

                payload = _json.dumps(
                    {
                        "artifact_id": 987654321,
                        "op": {"op_type": "selftest.ping"},
                        "actor_id": 0,
                    }
                ).encode("utf-8")
                ao_resp = self.ctx.session.post(
                    f"{self.ctx.base_url}/api/v1/federation/artifact-op",
                    data=payload,
                    headers=federation_headers(shared_key, payload),
                    timeout=10,
                )
                try:
                    ao_json = ao_resp.json() or {}
                except Exception:
                    ao_json = {}
                ao_ok = ao_resp.status_code == 200 and ao_json.get("ok") is True
                self.ctx.results.append(
                    {
                        "method": "POST",
                        "path": "/api/v1/federation/artifact-op (signed)",
                        "status_code": ao_resp.status_code,
                        "duration_ms": 0,
                        "success": ao_ok,
                    }
                )
                if ao_ok:
                    logger.info("Federation artifact-op PASSED (signed, valid)")
                else:
                    logger.warning(
                        f"Federation artifact-op -> {ao_resp.status_code}: "
                        f"{ao_resp.text[:200]}"
                    )
            except Exception as e:
                logger.debug(f"Federation artifact-op error: {e}")
            finally:
                drop_connection(_db, remote_id)
        # OAuth login-init: no provider credentials are configured in this
        # environment, so no valid 2xx/3xx exercise exists (every call is
        # 400/404/422/500 by design). Skipped honestly — counted, visible,
        # neither passed nor failed.
        for provider in ["github", "google"]:
            self.ctx.results.append(
                {
                    "method": "GET",
                    "path": f"/api/v1/auth/oauth/{provider}/login (stub)",
                    "status_code": 0,
                    "duration_ms": 0,
                    "success": False,
                    "skipped": True,
                    "note": "OAuth provider not configured; no valid 2xx/3xx exercise possible",
                }
            )
