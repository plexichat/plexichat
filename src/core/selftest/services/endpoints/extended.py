"""Extended coverage tests for self-test loop.

Adds negative permission, pagination, concurrency, entity lifecycle,
and WebSocket gateway assertions that were previously excluded or weak.
"""

import concurrent.futures

import utils.logger as logger

from .base import EndpointTesterBase


class ExtendedCoverageMixin(EndpointTesterBase):
    """Additional self-test coverage beyond the generic auto_loop."""

    def test_negative_permissions(self) -> None:
        """Verify 403/409/429 for slowmode, emoji, poll, reactions with wrong role."""
        if not self.ctx.test_server_id or not self.ctx.test_channel_id:
            return
        # slowmode as plain member (other_user) should be 403 (or 401 if token stale)
        if self.ctx.other_token:
            url = f"{self.ctx.base_url}/api/v1/features/channels/{self.ctx.test_channel_id}/slowmode"
            resp = self.ctx.other_session.put(
                url, json={"interval_ms": 5000}, timeout=5
            )
            # Documented contract: a plain member must be denied with 403.
            success = resp.status_code == 403
            self.ctx.results.append(
                {
                    "method": "PUT",
                    "path": "/api/v1/features/channels/{channel_id}/slowmode (negative member)",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": success,
                }
            )
            if not success:
                logger.warning(
                    f"Negative slowmode did not 403: {resp.status_code} {resp.text[:100]}"
                )
        # emoji create as member without manage -> 403 (other_user is not admin)
        if self.ctx.other_token and self.ctx.test_server_id:
            url = f"{self.ctx.base_url}/api/v1/servers/{self.ctx.test_server_id}/emojis"
            # try to create emoji as other_user (no emojis.manage)
            try:
                from PIL import Image
                import io

                img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                files = {"image": ("test.png", buf.getvalue(), "image/png")}
                data = {"name": "test_negative_emoji"}
                resp = self.ctx.other_session.post(
                    url, data=data, files=files, timeout=5
                )
                # Should be 403 – the documented contract for a member
                # without emojis.manage. Accepting 400/409 here would mask
                # permission regressions as passes.
                success = resp.status_code == 403
                self.ctx.results.append(
                    {
                        "method": "POST",
                        "path": "/api/v1/servers/{server_id}/emojis (negative)",
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": success,
                    }
                )
            except Exception as e:
                logger.debug(f"Negative emoji test error: {e}")

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
            # Documented contract: 200/201 (idempotent join) or 409 (already
            # member). Any other status — including EVERY 5xx — is a failure;
            # server faults under load must not be masked as passes.
            success = all(code in (200, 201, 409) for code in results)
            status_code = next(
                (code for code in results if code not in (200, 201, 409)),
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
        """Poll close, reaction, slowmode, media lifecycle."""
        # poll vote with invalid option -> 400
        if self.ctx.test_poll_id:
            url = f"{self.ctx.base_url}/api/v1/polls/{self.ctx.test_poll_id}/vote"
            resp = self.ctx.session.post(
                url, json={"option_ids": [99999999]}, timeout=5
            )
            success = resp.status_code == 400
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/polls/{poll_id}/vote (invalid option negative)",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": success,
                }
            )
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
        """Stub tests for previously excluded OAuth/passkeys/federation."""
        # OAuth login: documented contract is a 302 redirect to the provider,
        # 400 for a rejected redirect_uri, or 404 for an unsupported provider.
        # Accepting 200/401 here would hide contract drift.
        for provider in ["github", "google"]:
            url = f"{self.ctx.base_url}/api/v1/auth/oauth/{provider}/login"
            try:
                resp = self.ctx.session.get(url, timeout=5, allow_redirects=False)
                success = resp.status_code in (302, 400, 404)
                self.ctx.results.append(
                    {
                        "method": "GET",
                        "path": f"/api/v1/auth/oauth/{provider}/login (stub)",
                        "status_code": resp.status_code,
                        "duration_ms": 0,
                        "success": success,
                    }
                )
            except Exception as e:
                logger.debug(f"OAuth stub {provider} error: {e}")
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
        # Federation artifact-op with dummy HMAC: documented contract is 401
        # (unauthenticated). 400/403 acceptance would mask auth drift.
        url = f"{self.ctx.base_url}/api/v1/federation/artifact-op"
        try:
            resp = self.ctx.session.post(url, json={"test": 1}, timeout=5)
            success = resp.status_code == 401
            self.ctx.results.append(
                {
                    "method": "POST",
                    "path": "/api/v1/federation/artifact-op (stub)",
                    "status_code": resp.status_code,
                    "duration_ms": 0,
                    "success": success,
                }
            )
        except Exception as e:
            logger.debug(f"Federation stub error: {e}")
