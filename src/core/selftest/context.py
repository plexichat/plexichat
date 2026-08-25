"""
Shared context for SelfTestRunner services.

Holds all mutable state produced and consumed across
setup, discovery, cleanup, and endpoint-testing phases.
"""

from typing import List, Dict, Any, Optional


class SelfTestContext:
    """Mutable container for all self-test state."""

    def __init__(
        self,
        base_url: str,
        config: dict,
        requests_module: Any,
        standalone_mode: bool = True,
        unlicensed: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.config = config
        self.requests_module = requests_module
        self.standalone_mode = standalone_mode
        # True when the operator passed ``--unlicensed`` to the self-test
        # command. Free-tier detection (no license file on this machine) is
        # handled separately via discovery.plexijoin_expected_statuses().
        self.unlicensed = unlicensed

        self._test_password: Optional[str] = None
        self._setup_other_username: Optional[str] = None
        self._setup_other_email: Optional[str] = None
        self.token: Optional[str] = None
        self.other_token: Optional[str] = None
        self.test_user_id: Optional[int] = None
        self.test_other_user_id: Optional[int] = None
        self.test_third_user_id: Optional[int] = None
        self.test_server_id: Optional[int] = None
        self.test_channel_id: Optional[int] = None
        self.test_conversation_id: Optional[int] = None
        self.test_message_id: Optional[int] = None
        self.test_artifact_id: Optional[int] = None
        self.test_attachment_id: Optional[int] = None
        self.test_export_id: Optional[str] = None
        self.test_role_id: Optional[int] = None
        self.test_invite_code: Optional[str] = None
        self.test_second_invite_code: Optional[str] = None
        self.test_webhook_id: Optional[int] = None
        self.test_webhook_token: Optional[str] = None
        self.test_poll_id: Optional[int] = None
        self.test_poll_option_ids: Optional[List[int]] = None
        self.test_report_id: Optional[int] = None
        self.test_hash_report_id: Optional[int] = None
        self.test_message_report_id: Optional[int] = None
        self.test_notification_id: Optional[int] = None
        self.test_approval_id: Optional[int] = None
        self.test_application_id: Optional[int] = None
        self.test_bot_id: Optional[int] = None
        self.test_sticker_id: Optional[int] = None
        self.test_friend_request_id: Optional[int] = None
        self.test_bot_request_id: Optional[int] = None
        self.test_passkey_challenge_id: Optional[str] = None
        self.test_automod_rule_id: Optional[int] = None
        self.test_automod_violation_id: Optional[int] = None
        self.test_ticket_id: Optional[int] = None
        self.test_access_token_id: Optional[int] = None
        self.test_emoji_id: Optional[int] = None
        self.test_admin_role_id: Optional[int] = None
        self.test_non_system_role_id: Optional[int] = None
        self.test_admin_role_request_id: Optional[int] = None
        # An admin user that is NOT the same identity as the main
        # self-test session (the main session logs in as test_user_id,
        # with admin row at the same id).  The route handlers for
        # admin-to-admin privileged actions (force-password-change,
        # toggle-status, role-change, ...) refuse to act on the
        # caller themselves, so resolve_target_admin_id() looks up
        # this distinct test_admin_user_id instead of a fabricated
        # snowflake that would 404 in admin_users.
        self.test_admin_user_id: Optional[int] = None
        self.test_interaction_response_id: Optional[int] = None
        self.test_thread_id: Optional[int] = None
        self.test_dsar_id: Optional[int] = None
        self.test_log_filename: Optional[str] = None
        self.test_plexijoin_connection_id: Optional[int] = None
        self.test_plexijoin_request_id: Optional[int] = None
        self.test_plexijoin_deny_request_id: Optional[int] = None
        self.test_stash_collection_id: Optional[str] = None
        self.test_stash_item_id: Optional[str] = None
        self.test_sum_workbook_id: Optional[str] = None
        self.test_sum_sheet_id: Optional[str] = None
        self.test_sum_operation_id: Optional[str] = None

        self.results: List[Dict[str, Any]] = []
        self.start_time = 0.0
        self.ram_monitor: Any = None

        self._other_session_paths: set = set()
        self._admin_role_super_id: Optional[int] = None

        # Service references (populated by SelfTestRunner.__init__)
        self.data: Any = None
        self.discovery: Any = None
        self.cleanup: Any = None
        self.setup: Any = None
        self.endpoints: Any = None
        self.ws: Any = None
        self.artifacts: Any = None
        self.sum: Any = None
        self.ratelimit: Any = None
        self.static_client: Any = None
        self.docs: Any = None
        self.report: Any = None
        # Persistent journal of privileged test artifacts (grants/elevated
        # accounts) so a crashed run can be reconciled by the next run.
        self.journal: Any = None

        self.session = self.make_session()
        self.other_session = self.make_session()
        self.openapi_spec: Dict[str, Any] = {}

    def make_session(self, copy_headers_from: Any = None) -> Any:
        """Create a fresh HTTP session against the running server."""
        session = self.requests_module.Session()
        if copy_headers_from is not None:
            session.headers.update(copy_headers_from)
        return session

    def close_sessions(self) -> None:
        """Close all long-lived HTTP sessions (full teardown helper)."""
        for attr in ("session", "other_session"):
            session = getattr(self, attr, None)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
                setattr(self, attr, None)
