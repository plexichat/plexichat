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
        router.get("")(self.docs_index)
        router.get("/")(self.docs_index)

        router.get("/getting-started")(self.docs_getting_started)

        router.get("/deployment")(self.docs_deployment)
        router.get("/configuration")(self.docs_configuration)
        router.get("/features")(self.docs_features)
        router.get("/permissions")(self.docs_permissions)
        router.get("/security")(self.docs_security)
        router.get("/keyrings")(self.docs_keyrings)
        router.get("/performance")(self.docs_performance)

        router.get("/reference")(self.docs_api_reference)
        router.get("/reference/{page}")(self.docs_api_page)

        router.get("/websocket")(self.docs_websocket_index)
        router.get("/websocket/{page}")(self.docs_websocket_page)

        router.get("/rate-limits")(self.docs_rate_limits)
        router.get("/errors")(self.docs_errors)

        router.get("/security-logout")(self.docs_security_logout)
        router.get("/access-blocked")(self.docs_access_blocked)

        router.get("/data-types")(self.docs_data_types)
        router.get("/default-config")(self.docs_default_config)

        router.get("/config-authentication")(self.docs_config_authentication)
        router.get("/config-database")(self.docs_config_database)
        router.get("/config-redis")(self.docs_config_redis)
        router.get("/config-media")(self.docs_config_media)
        router.get("/config-voice")(self.docs_config_voice)
        router.get("/config-websocket")(self.docs_config_websocket)
        router.get("/config-search")(self.docs_config_search)
        router.get("/config-rate-limiting")(self.docs_config_rate_limiting)
        router.get("/config-api")(self.docs_config_api)
        router.get("/config-email")(self.docs_config_email)
        router.get("/config-embeds")(self.docs_config_embeds)

        router.get("/end-user/getting-started")(self.docs_end_user_getting_started)

        router.get("/deployment/overview")(self.docs_deployment_overview)
        router.get("/deployment/requirements")(self.docs_deployment_requirements)
        router.get("/deployment/getting-started")(self.docs_deployment_getting_started)
        router.get("/deployment/index")(self.docs_deployment_index)
        router.get("/deployment/postgres-migration")(
            self.docs_deployment_postgres_migration
        )
        router.get("/deployment/versioning")(self.docs_deployment_versioning)

        router.get("/migrations")(self.docs_migrations)
        router.get("/migration-reference")(self.docs_migration_reference)

        router.get("/admin")(self.docs_admin)
        router.get("/admin/getting-started")(self.docs_admin_getting_started)
        router.get("/admin/approval-workflows")(self.docs_admin_approval_workflows)
        router.get("/admin/audit-logging")(self.docs_admin_audit_logging)
        router.get("/admin/rbac")(self.docs_admin_rbac)
        router.get("/admin/server-management")(self.docs_admin_server_management)
        router.get("/admin/operations")(self.docs_admin_operations)
        router.get("/admin/troubleshooting")(self.docs_admin_troubleshooting)
        router.get("/admin/user-management")(self.docs_admin_user_management)
        router.get("/admin/security")(self.docs_admin_security)

        router.get("/client-development")(self.docs_client_development)
        router.get("/client-development/websocket")(self.docs_client_websocket)

        router.get("/end-user")(self.docs_end_user_index)
        router.get("/end-user/passkeys")(self.docs_end_user_passkeys)
        router.get("/end-user/password-guidance")(self.docs_end_user_password_guidance)
        router.get("/end-user/permissions")(self.docs_end_user_permissions)
        router.get("/end-user/two-factor-authentication")(self.docs_end_user_2fa)

        router.get("/reference/notifications")(self.docs_api_notifications)
        router.get("/reference/polls")(self.docs_api_polls)
        router.get("/reference/voice")(self.docs_api_voice)
        router.get("/reference/media")(self.docs_api_media)
        router.get("/reference/reports")(self.docs_api_reports)
        router.get("/reference/feedback")(self.docs_api_feedback)
        router.get("/reference/telemetry")(self.docs_api_telemetry)
        router.get("/reference/system")(self.docs_api_system)
        router.get("/reference/admin")(self.docs_api_admin)

        router.get("/deployment/env-generator")(self.docs_env_generator)

        router.get("/cli/overview")(self.docs_cli_overview)
