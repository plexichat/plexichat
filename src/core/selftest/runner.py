"""
Self-Test Runner - Automated API endpoint validation.

Discovers all registered routes from FastAPI and exercises them.
Supports automated authentication, retry logic, and traceback capture.
"""

import time
import re

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore

import utils.config as config
import utils.logger as logger

from .context import SelfTestContext
from .services.setup import SetupService
from .services.cleanup import CleanupService
from .services.data import DataGenerator
from .services.discovery import RouteDiscovery
from .services.endpoints import EndpointTester
from .services.websocket import WebSocketTester
from .services.ratelimit import RateLimitTester
from .services.static_client import StaticClientTester
from .services.docs import DocsTester
from .services.report import ReportGenerator


class SelfTestRunner:
    """Automated API test runner."""

    def __init__(self, base_url: str, standalone_mode: bool = True):
        if requests is None:
            raise RuntimeError("requests dependency is required for selftest runner")

        selftest_cfg = config.get("selftest", {})
        self.ctx = SelfTestContext(
            base_url=base_url,
            config=selftest_cfg,
            requests_module=requests,
            standalone_mode=standalone_mode,
        )

        # Service composition — each service operates on the shared context
        self.data = DataGenerator(self.ctx)
        self.discovery = RouteDiscovery(self.ctx)
        self.cleanup = CleanupService(self.ctx)
        self.setup = SetupService(self.ctx)
        self.endpoints = EndpointTester(self.ctx)
        self.ws = WebSocketTester(self.ctx)
        self.ratelimit = RateLimitTester(self.ctx)
        self.static_client = StaticClientTester(self.ctx)
        self.docs = DocsTester(self.ctx)
        self.report = ReportGenerator(self.ctx)

        # Expose services on context so they can call each other
        self.ctx.data = self.data
        self.ctx.discovery = self.discovery
        self.ctx.cleanup = self.cleanup
        self.ctx.setup = self.setup
        self.ctx.endpoints = self.endpoints
        self.ctx.ws = self.ws
        self.ctx.ratelimit = self.ratelimit
        self.ctx.static_client = self.static_client
        self.ctx.docs = self.docs
        self.ctx.report = self.report

    def run_all(self) -> bool:
        """Run all discovered API tests."""
        self.ctx.start_time = time.time()
        logger.info("=" * 60)
        logger.info("STARTING API SELF-TEST SUITE")
        logger.info(f"Target: {self.ctx.base_url}")
        logger.info("=" * 60)

        # 1. Discover Routes (and fetch OpenAPI spec)
        routes = self.discovery.discover_routes()
        if not routes:
            logger.error("No routes discovered. Aborting tests.")
            return False
        logger.info(f"Discovered {len(routes)} endpoints")

        # 2. Setup Auth and Resources
        self.cleanup.pre_test_cleanup()
        if not self.setup.run_setup():
            logger.error("Setup failed. Aborting tests.")
            return False

        # 3. WebSocket Test
        self.ws.test_websocket()

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

        for route in routes:
            path = route["path"]
            method = route["method"]

            if path in excluded or f"{method}:{path}" in excluded:
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

            if "/leave" in path and "/servers/" in path:
                if (
                    self.ctx.other_token
                    and self.ctx.test_channel_id
                    and self.ctx.test_server_id
                ):
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
                        if (
                            invite_resp.status_code in (200, 201)
                            and self.ctx.test_other_user_id
                        ):
                            invite_code = invite_resp.json().get("code")
                            if invite_code:
                                rejoin_url = f"{self.ctx.base_url}/api/v1/channels/invites/{invite_code}"
                                rejoin_resp = self.ctx.other_session.post(
                                    rejoin_url, timeout=5
                                )
                                if rejoin_resp.status_code in (200, 201, 204):
                                    use_other = True
                    except Exception:
                        pass

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

            if "{" in path or "}" in path:
                test_path = path
                placeholders = re.findall(r"\{([a-zA-Z0-9_]+)\}", path)

                for p_name in placeholders:
                    val = self.data.get_param_value(p_name, path)
                    test_path = test_path.replace(f"{{{p_name}}}", val)

                if "{" in test_path:
                    logger.debug(
                        f"Skipping endpoint with remaining placeholders: {method} {path}"
                    )
                    continue

                self.endpoints.test_endpoint(method, test_path, route, use_other)
            else:
                self.endpoints.test_endpoint(method, path, route, use_other)

            time.sleep(0.01)

        # Safety net: unblock admin user if still blocked from auto-loop
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
            self.endpoints.test_migration_endpoints()
            self.endpoints.test_access_token_rotate()
            self.endpoints.test_delay_deletion()
            self.endpoints.test_password_reset_confirm()
            self.endpoints.test_media_upload_complete()
            self.endpoints.test_dsar()
            self.endpoints.test_transcript_export()
            # Poll vote must run before poll close
            self.endpoints.test_poll_vote()
            self.endpoints.test_poll_close()
            # New standalone tests for previously-excluded endpoints
            self.endpoints.test_onboarding_preset()
            self.endpoints.test_new_migration_endpoints()
            self.endpoints.test_bot_creation_via_api()
            self.endpoints.test_user_2fa_flow()
            self.endpoints.test_admin_2fa_flow()
            self.endpoints.test_interaction_callback()
            self.endpoints.test_bulk_delete_messages()
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
                time.sleep(0.01)

        # 8. Explicit bot server flow
        self.endpoints.test_bot_server_integration()

        # 9. DELETE endpoint testing
        self.endpoints.test_delete_resources()

        # 10. Cleanup
        self.cleanup.cleanup_test_data()

        # 11. Summary
        success = self.report.report_summary()

        return success
