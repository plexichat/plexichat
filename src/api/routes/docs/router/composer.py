"""
DocsRouter composer - combines all mixins into the final class.

The DocsRouter class registers all documentation route handlers on a FastAPI
APIRouter instance via register_routes().
"""

from fastapi import APIRouter

from .base import DocsRouterBase
from .serving import ServingMixin
from .core_pages import CorePagesMixin
from .config_pages import ConfigPagesMixin
from .deployment_pages import DeploymentPagesMixin
from .admin_pages import AdminPagesMixin
from .reference_pages import ReferencePagesMixin
from .websocket_pages import WebSocketPagesMixin
from .user_pages import UserPagesMixin
from .env_generator import EnvGeneratorMixin
from .cli_pages import CliPagesMixin


class DocsRouter(
    ServingMixin,
    CorePagesMixin,
    ConfigPagesMixin,
    DeploymentPagesMixin,
    AdminPagesMixin,
    ReferencePagesMixin,
    WebSocketPagesMixin,
    UserPagesMixin,
    EnvGeneratorMixin,
    CliPagesMixin,
    DocsRouterBase,
):
    def register_routes(self, router: APIRouter) -> None:
        router.get("", include_in_schema=False)(self.docs_index)
        router.get("/", include_in_schema=False)(self.docs_index)

        router.get("/getting-started", include_in_schema=False)(
            self.docs_getting_started
        )

        router.get("/deployment", include_in_schema=False)(self.docs_deployment)
        router.get("/configuration", include_in_schema=False)(self.docs_configuration)
        router.get("/features", include_in_schema=False)(self.docs_features)
        router.get("/permissions", include_in_schema=False)(self.docs_permissions)
        router.get("/security", include_in_schema=False)(self.docs_security)
        router.get("/keyrings", include_in_schema=False)(self.docs_keyrings)
        router.get("/performance", include_in_schema=False)(self.docs_performance)

        router.get("/reference", include_in_schema=False)(self.docs_api_reference)
        router.get("/reference/{page}", include_in_schema=False)(self.docs_api_page)

        router.get("/websocket", include_in_schema=False)(self.docs_websocket_index)
        router.get("/websocket/{page}", include_in_schema=False)(
            self.docs_websocket_page
        )

        router.get("/rate-limits", include_in_schema=False)(self.docs_rate_limits)
        router.get("/errors", include_in_schema=False)(self.docs_errors)

        router.get("/security-logout", include_in_schema=False)(
            self.docs_security_logout
        )
        router.get("/access-blocked", include_in_schema=False)(self.docs_access_blocked)

        router.get("/data-types", include_in_schema=False)(self.docs_data_types)
        router.get("/default-config", include_in_schema=False)(self.docs_default_config)

        router.get("/config-authentication", include_in_schema=False)(
            self.docs_config_authentication
        )
        router.get("/config-database", include_in_schema=False)(
            self.docs_config_database
        )
        router.get("/config-valkey", include_in_schema=False)(self.docs_config_valkey)
        router.get("/config-media", include_in_schema=False)(self.docs_config_media)
        router.get("/config-voice", include_in_schema=False)(self.docs_config_voice)
        router.get("/config-websocket", include_in_schema=False)(
            self.docs_config_websocket
        )
        router.get("/config-search", include_in_schema=False)(self.docs_config_search)
        router.get("/config-rate-limiting", include_in_schema=False)(
            self.docs_config_rate_limiting
        )
        router.get("/config-api", include_in_schema=False)(self.docs_config_api)
        router.get("/config-email", include_in_schema=False)(self.docs_config_email)
        router.get("/config-embeds", include_in_schema=False)(self.docs_config_embeds)

        router.get("/end-user/getting-started", include_in_schema=False)(
            self.docs_end_user_getting_started
        )

        router.get("/deployment/overview", include_in_schema=False)(
            self.docs_deployment_overview
        )
        router.get("/deployment/requirements", include_in_schema=False)(
            self.docs_deployment_requirements
        )
        router.get("/deployment/getting-started", include_in_schema=False)(
            self.docs_deployment_getting_started
        )
        router.get("/deployment/index", include_in_schema=False)(
            self.docs_deployment_index
        )
        router.get("/deployment/postgres-migration", include_in_schema=False)(
            self.docs_deployment_postgres_migration
        )
        router.get("/deployment/versioning", include_in_schema=False)(
            self.docs_deployment_versioning
        )

        router.get("/migrations", include_in_schema=False)(self.docs_migrations)
        router.get("/migration-reference", include_in_schema=False)(
            self.docs_migration_reference
        )

        router.get("/admin", include_in_schema=False)(self.docs_admin)
        router.get("/admin/getting-started", include_in_schema=False)(
            self.docs_admin_getting_started
        )
        router.get("/admin/approval-workflows", include_in_schema=False)(
            self.docs_admin_approval_workflows
        )
        router.get("/admin/audit-logging", include_in_schema=False)(
            self.docs_admin_audit_logging
        )
        router.get("/admin/rbac", include_in_schema=False)(self.docs_admin_rbac)
        router.get("/admin/server-management", include_in_schema=False)(
            self.docs_admin_server_management
        )
        router.get("/admin/operations", include_in_schema=False)(
            self.docs_admin_operations
        )
        router.get("/admin/troubleshooting", include_in_schema=False)(
            self.docs_admin_troubleshooting
        )
        router.get("/admin/user-management", include_in_schema=False)(
            self.docs_admin_user_management
        )
        router.get("/admin/security", include_in_schema=False)(self.docs_admin_security)
        # Both pages are linked from docs/admin/index.md; without a route the
        # renderer has no target and silently demotes those links to plain text.
        router.get("/admin/data-export", include_in_schema=False)(
            self.docs_admin_data_export
        )
        router.get("/admin/purge-residue-check", include_in_schema=False)(
            self.docs_admin_purge_residue_check
        )

        router.get("/client-development", include_in_schema=False)(
            self.docs_client_development
        )
        router.get("/client-development/websocket", include_in_schema=False)(
            self.docs_client_websocket
        )

        router.get("/end-user", include_in_schema=False)(self.docs_end_user_index)
        router.get("/end-user/passkeys", include_in_schema=False)(
            self.docs_end_user_passkeys
        )
        router.get("/end-user/password-guidance", include_in_schema=False)(
            self.docs_end_user_password_guidance
        )
        router.get("/end-user/permissions", include_in_schema=False)(
            self.docs_end_user_permissions
        )
        router.get("/end-user/two-factor-authentication", include_in_schema=False)(
            self.docs_end_user_2fa
        )

        router.get("/reference/notifications", include_in_schema=False)(
            self.docs_api_notifications
        )
        router.get("/reference/polls", include_in_schema=False)(self.docs_api_polls)
        router.get("/reference/voice", include_in_schema=False)(self.docs_api_voice)
        router.get("/reference/media", include_in_schema=False)(self.docs_api_media)
        router.get("/reference/reports", include_in_schema=False)(self.docs_api_reports)
        router.get("/reference/feedback", include_in_schema=False)(
            self.docs_api_feedback
        )
        router.get("/reference/telemetry", include_in_schema=False)(
            self.docs_api_telemetry
        )
        router.get("/reference/system", include_in_schema=False)(self.docs_api_system)
        router.get("/reference/admin", include_in_schema=False)(self.docs_api_admin)

        router.get("/deployment/env-generator", include_in_schema=False)(
            self.docs_env_generator
        )

        router.get("/cli/overview", include_in_schema=False)(self.docs_cli_overview)

        router.get("/favicon.ico", include_in_schema=False)(self._favicon)

    async def _favicon(self):
        """Return a minimal SVG favicon to prevent 405 errors."""
        from fastapi.responses import Response

        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="6" fill="#6366f1"/>'
            '<text x="16" y="23" font-size="20" text-anchor="middle" fill="#fff" '
            'font-family="sans-serif" font-weight="bold">P</text></svg>'
        )
        return Response(content=svg, media_type="image/svg+xml")
