"""SetupService composition class.

Combines all mixins into a single SetupService class used by SelfTestRunner.
"""

import traceback

import src.api as api
import utils.logger as logger

from .base import SetupServiceBase
from .auth import AuthSetupMixin
from .admin import AdminSetupMixin
from .server import ServerSetupMixin
from .media import MediaSetupMixin
from .features import FeatureSetupMixin


class SetupService(
    AuthSetupMixin,
    AdminSetupMixin,
    ServerSetupMixin,
    MediaSetupMixin,
    FeatureSetupMixin,
    SetupServiceBase,
):
    """Creates all test resources needed for a selftest run.

    Orchestrates the full setup flow: users, admin roles, server/channel,
    messages, media, features (notifications, reports, automod, tokens,
    tickets, polls, friend requests, emoji, stickers, threads).
    """

    def run_setup(self) -> bool:
        try:
            self.setup_security_headers()

            auth_mod = api.get_auth()
            servers_mod = api.get_servers()
            if not auth_mod or not servers_mod:
                logger.error("Auth or Servers module not available for self-test")
                return False

            self._ensure_password()

            if not self.create_main_user():
                return False

            enable_admin = self.ctx.config.get("enable_admin_tests", True)
            if enable_admin:
                self.grant_admin_permissions()
                self.setup_admin_user_and_roles()

            self.create_other_user()
            if self.ctx.test_other_user_id:
                self.setup_other_admin_user()
            logger.debug(f"Test other user ID: {self.ctx.test_other_user_id}")

            if not self.login_main_user():
                return False
            self.login_other_user()
            self.setup_other_session_paths()
            self.block_selftest_username_prefix()

            if not self.create_server_and_channel():
                return False
            self.create_test_message()
            self.create_test_attachment()
            self.create_test_export()
            self.create_test_role()
            self.create_test_invite()
            self.join_other_user_to_server()
            self.create_test_webhook()
            self.create_dummy_test_file()
            self.resolve_log_filename()
            self.create_test_settings()
            self.create_test_application_and_bot()
            self.create_test_notification()
            self.create_test_reports()
            self.create_test_automod_rule()
            self.create_test_access_token()
            self.create_test_ticket()
            self.create_test_poll()
            self.create_test_friend_request()
            self.create_test_emoji()
            self.create_test_sticker()
            self.create_test_thread()
            self.create_test_plexijoin_resources()
            self.ensure_approval_comments_table()

            logger.info(
                f"Resources created: Server={self.ctx.test_server_id}, Channel={self.ctx.test_channel_id}, Role={self.ctx.test_role_id}, "
                f"AutoMod={self.ctx.test_automod_rule_id}, Token={self.ctx.test_access_token_id}, Ticket={self.ctx.test_ticket_id}, "
                f"Poll={self.ctx.test_poll_id}, Thread={self.ctx.test_thread_id}"
            )
            return True
        except Exception as e:
            logger.error(f"Setup error: {e}")
            logger.error(traceback.format_exc())
            return False
