"""
Self-Test Runner - Automated API endpoint validation.

Discovers all registered routes from FastAPI and exercises them.
Supports automated authentication, retry logic, and traceback capture.
"""

import time
import re
from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor
import importlib

try:
    requests: Any = importlib.import_module("requests")
    HAS_REQUESTS = True
except ImportError:
    requests = None
    HAS_REQUESTS = False

import utils.config as config
import utils.logger as logger

from .context import SelfTestContext
from .artifact_journal import ArtifactJournal
from .services.setup import SetupService

from .services.cleanup import CleanupService
from .services.data import DataGenerator
from .services.discovery import RouteDiscovery
from .services.endpoints import EndpointTester
from .services.websocket import WebSocketTester
from .services.artifacts import ArtifactsTester
from .services.sum import SumTester
from .services.ratelimit import RateLimitTester
from .services.static_client import StaticClientTester
from .services.docs import DocsTester
from .services.report import ReportGenerator
from .services.memory import RamMonitor


class SelfTestRunner:
    """Automated API test runner."""

    def __init__(
        self,
        base_url: str,
        standalone_mode: bool = True,
        unlicensed: bool = False,
        status_only: bool = False,
    ):
        if requests is None:
            raise RuntimeError("requests dependency is required for selftest runner")

        # Status mode runs without app config (it can target a remote
        # deployment): no config lookups here.
        selftest_cfg = {} if status_only else config.get("selftest", {})
        self.status_only = status_only
        self.ctx = SelfTestContext(
            base_url=base_url,
            config=selftest_cfg,
            requests_module=requests,
            standalone_mode=standalone_mode,
            unlicensed=unlicensed,
            status_only=status_only,
        )
        self.ctx.journal = ArtifactJournal(selftest_cfg.get("journal_path"))

        # Service composition — each service operates on the shared context
        self.data = DataGenerator(self.ctx)
        self.discovery = RouteDiscovery(self.ctx)
        self.cleanup = CleanupService(self.ctx)
        self.setup = SetupService(self.ctx)
        self.endpoints = EndpointTester(self.ctx)
        self.ws = WebSocketTester(self.ctx)
        self.artifacts = ArtifactsTester(self.ctx)
        self.sum = SumTester(self.ctx)
        self.ratelimit = RateLimitTester(self.ctx)
        self.static_client = StaticClientTester(self.ctx)
        self.docs = DocsTester(self.ctx)
        self.report = ReportGenerator(self.ctx)
        self.ram_monitor = RamMonitor()

        # Expose services on context so they can call each other
        self.ctx.data = self.data
        self.ctx.discovery = self.discovery
        self.ctx.cleanup = self.cleanup
        self.ctx.setup = self.setup
        self.ctx.endpoints = self.endpoints
        self.ctx.ws = self.ws
        self.ctx.artifacts = self.artifacts
        self.ctx.sum = self.sum
        self.ctx.ratelimit = self.ratelimit
        self.ctx.static_client = self.static_client
        self.ctx.docs = self.docs
        self.ctx.report = self.report
        self.ctx.ram_monitor = self.ram_monitor

        # Unlicensed/status-only modes are EXPLICIT-ONLY and already passed
        # through SelfTestContext above: the runner treats PlexiJoin
        # endpoints as license-gated only when the operator passes
        # ``--unlicensed``. There is deliberately no auto-detection from
        # the license state of the machine -- a missing license file must
        # surface as self-test failure unless the operator opts in, so
        # CI and operators can rely on the flag being the sole switch.

    def _resolve_test_path(self, path: str) -> Optional[str]:
        """Substitute path placeholders with concrete test values.

        Returns ``None`` if any placeholder remains unresolvable. Placeholders
        must be resolved at request time (not during route collection) because
        some values are captured mid-run — e.g. ``test_artifact_id`` is only set
        when the auto-loop POST /artifacts executes.
        """
        if "{" not in path and "}" not in path:
            return path
        test_path = path
        for p_name in re.findall(r"\{([a-zA-Z0-9_]+)\}", path):
            val = self.data.get_param_value(p_name, path)
            test_path = test_path.replace(f"{{{p_name}}}", val)
        if "{" in test_path:
            return None
        return test_path

    def _stabilize_thread(self, test_path: str) -> None:
        """Best-effort reset of archive/lock state before membership probes.

        Extracts the thread id from a resolved ``/api/v1/threads/{id}/...``
        path and calls unarchive + unlock with the main (owner) session so
        the upcoming probe exercises the intended code path instead of
        failing on state an earlier alphabetical probe left behind.
        Never raises: a failed stabilizer leaves the probe as-is.
        """
        match = re.search(r"/api/v1/threads/(\d+)", test_path)
        if not match:
            return
        base = f"{self.ctx.base_url}/api/v1/threads/{match.group(1)}"
        for action in ("unarchive", "unlock"):
            try:
                self.ctx.session.post(f"{base}/{action}", timeout=5)
            except Exception as e:
                logger.debug(f"Thread stabilize {action} note: {e}")

    def _prepare_leave_other_user(self) -> bool:
        """Make other_user a re-joinable member so POST .../servers/{id}/leave
        can be exercised with other_session (the main admin cannot leave).

        Runs immediately before the leave request so the rejoin is still valid.
        """
        if not (
            self.ctx.other_token
            and self.ctx.test_channel_id
            and self.ctx.test_server_id
        ):
            return False
        try:
            unban_url = f"{self.ctx.base_url}/api/v1/servers/{self.ctx.test_server_id}/bans/{self.ctx.test_other_user_id}"
            self.ctx.session.delete(unban_url, timeout=5)
        except Exception:
            pass
        try:
            invite_create_url = f"{self.ctx.base_url}/api/v1/channels/{self.ctx.test_channel_id}/invites"
            invite_resp = self.ctx.session.post(
                invite_create_url, json={"max_uses": 10}, timeout=5
            )
            if invite_resp.status_code in (200, 201) and self.ctx.test_other_user_id:
                invite_code = invite_resp.json().get("code")
                if invite_code:
                    rejoin_url = (
                        f"{self.ctx.base_url}/api/v1/channels/invites/{invite_code}"
                    )
                    rejoin_resp = self.ctx.other_session.post(rejoin_url, timeout=5)
                    # 409 means already a member (e.g. restored by an
                    # earlier hook) — equally good for the leave test.
                    if rejoin_resp.status_code in (200, 201, 204, 409):
                        return True
        except Exception:
            pass
        return False

    def run_all(self) -> bool:
        """Run all discovered API tests (or the status subset)."""
        try:
            if self.status_only:
                return self._run_status()
            return self._run_all()
        except Exception:
            # Error paths must not leak the RAM monitor thread.
            try:
                self.ram_monitor.stop()
            except Exception:
                pass
            raise

    def _run_status(self) -> bool:
        """Read-only liveness checks, safe for cron/Prometheus polling.

        No authentication, no mutations, no fixtures, no test allowances:
        every request goes through production middleware exactly as a
        status page would. Safe to aim at a live deployment.
        """
        from src.cli.status_probe import run_status_checks

        self.ctx.start_time = time.time()
        logger.info("=" * 60)
        logger.info("STATUS CHECK (read-only, unauthenticated)")
        logger.info(f"Target: {self.ctx.base_url}")
        logger.info("=" * 60)

        for result in run_status_checks(self.ctx.base_url):
            self.ctx.results.append(result)
            if result["success"]:
                logger.info(
                    f"Status {result['label']} PASSED -> {result['status_code']}"
                )
            else:
                logger.warning(f"Status {result['label']} -> {result['status_code']}")
        return self.report.report_summary()

    def _run_all(self) -> bool:
        """Run all discovered API tests."""
        self.ctx.start_time = time.time()
        self.ram_monitor.start()
        logger.info("=" * 60)
        logger.info("STARTING API SELF-TEST SUITE")
        logger.info(f"Target: {self.ctx.base_url}")
        logger.info("=" * 60)

        # 1. Discover Routes (and fetch OpenAPI spec)
        routes = self.discovery.discover_routes()
        if not routes:
            logger.error("No routes discovered. Aborting tests.")
            self.ram_monitor.stop()
            return False
        logger.info(f"Discovered {len(routes)} endpoints")

        # 2. Setup Auth and Resources
        self.cleanup.pre_test_cleanup()
        if not self.setup.run_setup():
            logger.error("Setup failed. Aborting tests.")
            self.ram_monitor.stop()
            return False

        # 3. WebSocket Test
        self.ws.test_websocket()

        # 3b. Artifacts feature self-test suites (lifecycle, capabilities,
        #     retention + privacy, and the WSS artifact-op round-trip harness).
        self.artifacts.test_artifacts()

        # 3c. PlexiSum realtime fabric (capability-gated WSS SUM_OP round trip).
        self.sum.test_sum()

        # 4. Static client smoke tests (no-op when feature is disabled)
        self.static_client.test_static_client()

        # 4b. Documentation server smoke tests
        self.docs.test_docs()

        # 5. Rate Limit Test (early, while auth creds are fresh)
        self.ratelimit.test_rate_limits()

        # 5. Execute API Tests
        excluded = set(self.ctx.config.get("excluded_endpoints", []))
        # Auth endpoints that would invalidate the main session if tested in the main loop.
        # These are tested via dedicated standalone methods instead.
        # --- Endpoints excluded from auto-loop but tested via standalone tests ---
        excluded.add("POST:/api/v1/auth/login")
        excluded.add("POST:/api/v1/auth/logout")
        excluded.add("POST:/api/v1/auth/register")
        excluded.add("POST:/api/v1/auth/sessions/revoke-all")
        excluded.add("POST:/api/v1/admin/logout")
        excluded.add("POST:/api/v1/admin/login")

        # logout-all logs out EVERY user on the platform and cannot be scoped.
        excluded.add("POST:/api/v1/admin/security/logout-all")

        # Bot creation is done during setup via create_bot_for_application
        excluded.add("POST:/api/v1/applications/{application_id}/bot")

        # Bot server membership endpoints — tested via test_bot_server_integration()
        # which uses other_session (other_user is the server member who interacts with bot)
        excluded.add("POST:/api/v1/bots/servers/{server_id}/request")
        excluded.add("POST:/api/v1/bots/servers/{server_id}/approve")
        excluded.add("PUT:/api/v1/bots/servers/{server_id}/requests/{request_id}")
        excluded.add("POST:/api/v1/bots/servers/{server_id}/requests/{request_id}")

        # PlexiStash and PlexiSum are stateful domains. Their routes require
        # IDs and revisions returned by earlier requests, so they are exercised
        # by test_suite_domains() rather than the generic OpenAPI fuzzer.
        excluded.add("/api/v1/suite/stash")
        excluded.add("/api/v1/suite/sum")

        # Inbound federation routes authenticate with an HMAC signature over
        # the raw body (the remote instance's shared key), not a local JWT, so
        # the generic OpenAPI fuzzer cannot satisfy their auth and would always
        # get 401. They are exercised end-to-end (real encrypted connection +
        # signed payload -> 200) by ``_test_federation_bridge`` in the artifacts
        # self-test service, so skipping them here is not a coverage gap.
        excluded.add("POST:/api/v1/federation/artifact-op")
        excluded.add("POST:/api/v1/federation/artifact-event")

        # These are exercised during setup (other_user joins via invite,
        # other_user sends friend request) so we avoid double-testing
        # and spurious 409/404 failures in the auto-loop.
        excluded.add("POST:/api/v1/channels/invites/{invite_code}")
        excluded.add("POST:/api/v1/relationships")

        if not self.ctx.config.get("enable_admin_tests", True):
            logger.info("Admin tests disabled -- excluding all /admin/ endpoints")
            for route in routes:
                if "/admin/" in route["path"]:
                    excluded.add(f"{route['method']}:{route['path']}")

        # --- License-gated endpoints ---
        # Without a valid license, PlexiJoin (the licensed feature) rejects
        # every /api/v1/admin/plexijoin/* route with 403 and the inbound
        # artifact federation routes (/api/v1/federation/artifact-*) reject
        # with 403 as well because they are gated by the same feature check
        # (via the shared PlexiJoin manager). When the operator explicitly
        # passes ``--unlicensed`` those routes are excluded so the suite can
        # still complete against an unlicensed deployment; otherwise missing
        # license is treated as a test failure (it surfaced as 403 here).
        #
        # The generic auto-loop cannot sign the HMAC payloads these routes
        # require, so the artifact federation endpoints are excluded from the
        # auto-loop regardless of license state (they are exercised end-to-end
        # by ``_test_federation_bridge`` in the artifacts service).
        if self.ctx.unlicensed:
            logger.info(
                "Unlicensed mode (explicit flag): excluding license-gated "
                "/plexijoin/ and /federation/artifact-* endpoints from the suite"
            )
            for route in routes:
                if "/plexijoin/" in route["path"] or route["path"].startswith(
                    "/api/v1/federation/artifact-"
                ):
                    excluded.add(f"{route['method']}:{route['path']}")

        # --- Browser-required endpoints (cannot be tested via HTTP API) ---
        # OAuth requires browser redirect flow
        excluded.add("GET:/api/v1/auth/oauth/{provider}/login")
        excluded.add("POST:/api/v1/auth/oauth/{provider}/callback")

        # Passkeys require WebAuthn browser API (navigator.credentials.create/get)
        excluded.add("POST:/api/v1/auth/passkeys/options/register")
        excluded.add("POST:/api/v1/auth/passkeys/register")
        excluded.add("POST:/api/v1/auth/passkeys/options/authenticate")
        excluded.add("POST:/api/v1/auth/passkeys/authenticate")
        excluded.add("GET:/api/v1/auth/passkeys")
        excluded.add("DELETE:/api/v1/auth/passkeys/{passkey_id}")
        excluded.add("PATCH:/api/v1/auth/passkeys/{passkey_id}")

        # Public license export requires cryptographic challenge-response flow
        # (Ed25519 signature of challenge nonce)
        excluded.add("GET:/public/license/export")
        excluded.add("GET:/api/v1/public/license/export")

        # --- Approval-gated endpoints (covered by dedicated ceremony tests
        # with real multi-admin approvals instead of the generic fuzzer,
        # which cannot satisfy single-use approval gates) ---
        for _ep in [
            "POST:/api/v1/admin/database/migrations/apply-all",
            "POST:/api/v1/admin/database/migrations/apply/{version}",
            "POST:/api/v1/admin/database/migrations/rollback/{version}",
            "POST:/api/v1/admin/migrations/{version}/run",
            "POST:/api/v1/admin/migrations/{version}/rollback",
            "POST:/api/v1/admin/migrations/emergency-override",
            "POST:/api/v1/admin/search/reindex",
            "POST:/api/v1/admin/search/reindex/standalone",
        ]:
            excluded.add(_ep)

        # --- Stateful lifecycles (covered by dedicated tests that honour
        # state ordering; the generic fuzzer hits them in arbitrary order) ---
        for _ep in [
            # access-token rotate->revoke->unrevoke lifecycle
            "POST:/api/v1/admin/security/access-tokens/{token_id}/rotate",
            "POST:/api/v1/admin/security/access-tokens/{token_id}/revoke",
            "POST:/api/v1/admin/security/access-tokens/{token_id}/unrevoke",
            # admin DSAR lifecycle (approve/deny/generate need pending rows)
            "POST:/api/v1/admin/data-export/{request_id}/approve",
            "POST:/api/v1/admin/data-export/{request_id}/deny",
            "POST:/api/v1/admin/data-export/{request_id}/generate",
            "GET:/api/v1/admin/data-export/{request_id}",
            # email verification (fuzzer token can never be valid; the
            # rejection contract is pinned by test_verify_email_contract)
            "POST:/api/v1/auth/verify-email",
        ]:
            excluded.add(_ep)

        # --- HMAC-signed federation + /Join contract (covered by dedicated
        # signed/seeded tests; the generic fuzzer cannot sign) ---
        for _ep in [
            "POST:/api/v1/federation/join-request",
            "POST:/api/v1/federation/message",
            "POST:/api/v1/Join/request",
            "POST:/api/v1/Join/{join_id}/message",
            "POST:/api/v1/Join/{join_id}/break",
            "POST:/api/v1/Join/{join_id}/approve",
            "POST:/api/v1/Join/{join_id}/reject",
            "GET:/api/v1/Join/list",
        ]:
            excluded.add(_ep)

        # --- Endpoints tested via dedicated standalone methods ---
        for _ep in [
            "POST:/api/v1/admin/database/migrations/apply/{version}",
            "POST:/api/v1/admin/database/migrations/rollback/{version}",
            "POST:/api/v1/admin/migrations/{version}/rollback",
            "POST:/api/v1/admin/migrations/{version}/run",
            "PATCH:/api/v1/admin/security/access-tokens/{token_id}/rotate",
            "POST:/api/v1/admin/security/access-tokens/{token_id}/revoke",
            "POST:/api/v1/admin/users/{user_id}/delay-deletion",
            "POST:/api/v1/auth/password-reset/confirm",
            "POST:/api/v1/media/upload/complete/{session_id}",
            "POST:/api/v1/polls/{poll_id}/vote",
            "POST:/api/v1/polls/{poll_id}/close",
            # DSAR endpoints are tested via test_dsar() standalone method
            "POST:/api/v1/users/@me/data-export",
            "GET:/api/v1/users/@me/data-export",
            "GET:/api/v1/users/@me/data-export/{request_id}",
            "DELETE:/api/v1/users/@me/data-export/{request_id}",
            "GET:/api/v1/users/@me/data-export/{request_id}/download",
            # 2FA endpoints would leave session state broken if run in auto-loop
            "POST:/api/v1/auth/2fa",
            "POST:/api/v1/auth/2fa/enable",
            "POST:/api/v1/auth/2fa/confirm",
            "POST:/api/v1/auth/2fa/disable",
            "GET:/api/v1/auth/2fa/status",
            # Admin 2FA — same state-leak concern
            "/api/v1/admin/auth/2fa/begin-setup",
            "/api/v1/admin/auth/2fa/disable",
            "/api/v1/admin/auth/2fa/regenerate-backup-codes",
            "/api/v1/admin/verify-otp",
            # Interaction callback needs a seeded DB interaction (not available generically)
            "POST:/api/v1/applications/interactions/{interaction_token}/callback",
            # Onboarding preset needs valid server+perms (tested in standalone)
            "POST:/api/v1/features/onboarding/apply-preset",
        ]:
            excluded.add(_ep)

        destructive_paths = {
            "/api/v1/admin/security/lock-user",
            "/api/v1/admin/security/unlock-user",
            "/api/v1/admin/security/force-logout",
            # logout-all is intentionally excluded: it logs out EVERY user on the
            # platform and cannot be scoped to other_user. It would invalidate the
            # main self-test session and break subsequent cleanup.
            "/api/v1/admin/admin-users/{user_id}/toggle-status",
            "/api/v1/admin/users/{user_id}/force-purge",
            "/api/v1/admin/users/{user_id}/force-username-change",
            # Clear-reactions is deferred (auto-fired in the destructive phase);
            # it only removes reactions and is safe to exercise generally.
            "DELETE:/api/v1/channels/{channel_id}/messages/{message_id}/reactions",
            # Bulk-delete is a destructive batch operation that removes the tracked
            # test message; deferred so it runs after the message is no longer
            # needed by other tests, then the delete_batch phase validates it.
            "POST:/api/v1/channels/{channel_id}/messages/bulk-delete",
        }

        if not self.ctx.standalone_mode:
            logger.info(
                "Non-standalone mode: skipping destructive endpoints "
                f"({len(destructive_paths)} deferred/destructive endpoints excluded)"
            )

        logger.info("Executing API tests...")

        deferred_destructive = []

        stateful_suite_prefixes = (
            "/api/v1/suite/stash",
            "/api/v1/suite/sum",
        )

        work_items = []
        for route in routes:
            path = route["path"]
            method = route["method"]

            if (
                path in excluded
                or f"{method}:{path}" in excluded
                or path.startswith(stateful_suite_prefixes)
            ):
                logger.debug(f"Skipping excluded endpoint: {method} {path}")
                continue

            if path in destructive_paths or f"{method}:{path}" in destructive_paths:
                if self.ctx.standalone_mode:
                    deferred_destructive.append(route)
                    logger.debug(f"Deferring destructive endpoint: {method} {path}")
                else:
                    logger.debug(
                        f"Skipping destructive endpoint (non-standalone): {method} {path}"
                    )
                continue

            if method == "DELETE":
                logger.debug(f"Skipping DELETE endpoint: {path}")
                continue

            use_other = False
            if self.ctx.other_token and self.ctx._other_session_paths:
                for other_path in self.ctx._other_session_paths:
                    if other_path in path or path.startswith(other_path):
                        use_other = True
                        break

            # Approvals created by main admin — must use other_session to approve
            if (
                self.ctx.other_token
                and "/approvals/" in path
                and ("/approve" in path or "/reject" in path)
            ):
                use_other = True

            # Threads: owner cannot leave own thread (403). Use other_user (member)
            # for leave so the auto_loop tests a valid permission path.
            # Join stays on main session (owner already member, idempotent 200).
            if (
                self.ctx.other_token
                and "/threads/" in path
                and method == "POST"
                and "/leave" in path
            ):
                use_other = True

            # Relationship accept is tested with the MAIN session (admin user
            # accepts the request sent by other_user during setup).

            if "/voice/" in path and method == "GET":
                logger.debug(
                    f"Skipping voice endpoint (no connection): {method} {path}"
                )
                continue

            if "/media/upload/chunk" in path:
                logger.debug(
                    f"Skipping chunk upload endpoint (requires real session): {method} {path}"
                )
                continue

            # Detect untestable routes now, but defer value substitution to
            # request time: some values (e.g. test_artifact_id) are only
            # captured while the auto-loop runs.
            if "{" in path or "}" in path:
                if self._resolve_test_path(path) is None:
                    logger.debug(
                        f"Skipping endpoint with remaining placeholders: {method} {path}"
                    )
                    continue

            work_items.append((method, path, route, use_other))

        # Run mutating endpoints sequentially first (they may create resources
        # that later reads depend on), then run read-only GETs in parallel.
        # Placeholders are resolved here, immediately before each request, so
        # IDs captured by earlier requests in the loop are used.
        for method, path, route, use_other in work_items:
            if method == "GET":
                continue
            if "/leave" in path and "/servers/" in path:
                if self._prepare_leave_other_user():
                    use_other = True
            test_path = self._resolve_test_path(path)
            if test_path is None:
                continue
            # Thread membership/message probes are order-sensitive: the
            # alphabetically earlier archive/lock probes flip thread state,
            # which turns join/leave/members/messages into 403/409. Restore a
            # known-active state before probing them.
            if (
                "/threads/" in test_path
                and method == "POST"
                and any(
                    seg in test_path
                    for seg in ("/join", "/leave", "/members", "/messages")
                )
            ):
                self._stabilize_thread(test_path)
            self.endpoints.test_endpoint(method, test_path, route, use_other)

        parallel_gets = [item for item in work_items if item[0] == "GET"]
        if parallel_gets:
            max_workers = int(self.ctx.config.get("parallel_get_workers", 8))
            workers = max(1, min(max_workers, len(parallel_gets)))

            def _process_chunk(chunk):
                sessions = {}
                try:
                    for method, raw_path, route, use_other in chunk:
                        test_path = self._resolve_test_path(raw_path)
                        if test_path is None:
                            continue
                        session = sessions.get(use_other)
                        if session is None:
                            src = (
                                self.ctx.other_session
                                if use_other
                                else self.ctx.session
                            )
                            session = self.ctx.make_session(
                                copy_headers_from=src.headers
                            )
                            sessions[use_other] = session
                        self.endpoints.test_endpoint(
                            method, test_path, route, use_other, session=session
                        )
                finally:
                    for session in sessions.values():
                        try:
                            session.close()
                        except Exception:
                            pass

            chunks = [[] for _ in range(workers)]
            for index, item in enumerate(parallel_gets):
                chunks[index % workers].append(item)

            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(_process_chunk, chunks))
        if self.ctx.test_user_id:
            try:
                resp = self.ctx.session.delete(
                    f"{self.ctx.base_url}/api/v1/admin/blocked-users/{self.ctx.test_user_id}",
                    timeout=5,
                )
                if resp.status_code in (200, 204):
                    logger.info("Safety unblocked test user from uploads")
            except Exception:
                pass

        # 6. Standalone-specific endpoint tests (before destructive, so delay-deletion
        #    and password-reset can use the other_user before they are force-purged)
        if self.ctx.standalone_mode:
            self.endpoints.test_auth_endpoints()
            self.endpoints.test_verify_email_contract()
            self.endpoints.test_migration_endpoints()
            self.endpoints.test_access_token_rotate()
            self.endpoints.test_approval_gated_actions()
            self.endpoints.test_federation_inbound()
            self.endpoints.test_join_contract()
            self.endpoints.test_new_migration_endpoints()
            self.endpoints.test_bot_creation_via_api()
            # Bot server flow needs other_user alive (non-owner requester;
            # self-review is forbidden) — must precede password-reset and
            # delay-deletion, which destroy that identity.
            self.endpoints.test_bot_server_integration()
            # Extended coverage runs BEFORE delay-deletion/password-reset:
            # concurrency and pagination probes run as other_user, and
            # those tests destroy that identity. Denial contracts are
            # unit-tested; every check here sends valid data and
            # expects 2xx.
            self.endpoints.test_pagination_walks()
            self.endpoints.test_concurrency_threads()
            self.endpoints.test_entity_lifecycle()
            self.endpoints.test_privileged_lifecycle()
            self.endpoints.test_websocket_thread_dispatch()
            self.endpoints.test_oauth_passkeys_federation_stubs()
            self.endpoints.test_password_reset_confirm()
            self.endpoints.test_delay_deletion()
            self.endpoints.test_media_upload_complete()
            self.endpoints.test_dsar()
            self.endpoints.test_transcript_export()
            # Poll vote must run before poll close
            self.endpoints.test_poll_vote()
            self.endpoints.test_poll_close()
            # New standalone tests for previously-excluded endpoints
            self.endpoints.test_onboarding_preset()
            # Bot server flow already ran before delay-deletion (needs a
            # live non-owner requester); nothing deferred here.
            self.endpoints.test_user_2fa_flow()
            self.endpoints.test_admin_2fa_flow()
            self.endpoints.test_interaction_callback()
            self.endpoints.test_suite_domains()
            self.endpoints.test_bulk_delete_messages()
            # Extended coverage ran earlier (before delay-deletion destroyed
            # other_user); nothing deferred here.
        else:
            logger.info(
                "Skipping standalone-specific endpoint tests (not in standalone mode)"
            )

        # 7. Deferred destructive endpoints (after standalone tests, so other_user
        #    still exists for lock/unlock/force-purge)
        if deferred_destructive:
            logger.info(
                "Executing deferred destructive endpoints (targeting other user)..."
            )
            for route in deferred_destructive:
                method = route["method"]
                path = route["path"]
                test_path = path
                for p_name in re.findall(r"\{([a-zA-Z0-9_]+)\}", path):
                    test_path = test_path.replace(
                        f"{{{p_name}}}", self.data.get_param_value(p_name, path)
                    )
                self.endpoints.test_endpoint(method, test_path, route)

        # 8. Explicit bot server flow (standalone already ran it before
        # delay-deletion; only run here outside standalone mode).
        if not self.ctx.standalone_mode:
            self.endpoints.test_bot_server_integration()

        # 9. DELETE endpoint testing
        self.endpoints.test_delete_resources()

        # 10. Cleanup
        self.cleanup.cleanup_test_data()

        # 11. Summary
        success = self.report.report_summary()

        self.ram_monitor.stop()

        return success
