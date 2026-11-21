import json
from typing import Optional, List

import utils.logger as logger
from src.core.base import SnowflakeID

from ..models import ApplicationInstallation
from ..exceptions import (
    ApplicationNotFoundError,
    InstallationExistsError,
    InstallationNotFoundError,
    PermissionDeniedError,
)
from .row_mappers import row_to_installation
from .protocol import ApplicationManagerProtocol


class InstallationMixin(ApplicationManagerProtocol):
    def install_application(
        self,
        application_id: SnowflakeID,
        server_id: SnowflakeID,
        installer_id: SnowflakeID,
        permissions: str = "0",
        scopes: Optional[List[str]] = None,
    ) -> ApplicationInstallation:
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        existing = self._db.fetch_one(
            "SELECT id FROM app_installations WHERE application_id = ? AND server_id = ?",
            (application_id, server_id),
        )
        if existing:
            raise InstallationExistsError(
                "Application is already installed on this server"
            )

        installation_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO app_installations
               (id, application_id, server_id, installer_id, permissions, scopes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                installation_id,
                application_id,
                server_id,
                installer_id,
                permissions,
                json.dumps(scopes or []),
                now,
                now,
            ),
        )

        if app.bot_id and self._servers:
            try:
                self._servers.add_member(server_id, app.bot_id)
            except Exception as e:
                logger.warning(f"Failed to add bot to server: {e}")

        logger.info(f"Application {application_id} installed on server {server_id}")

        return ApplicationInstallation(
            id=installation_id,
            application_id=application_id,
            server_id=server_id,
            installer_id=installer_id,
            permissions=permissions,
            scopes=scopes or [],
            created_at=now,
            updated_at=now,
        )

    def uninstall_application(
        self,
        application_id: SnowflakeID,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
    ) -> bool:
        installation = self._db.fetch_one(
            "SELECT id, installer_id FROM app_installations "
            "WHERE application_id = ? AND server_id = ?",
            (application_id, server_id),
        )
        if not installation:
            raise InstallationNotFoundError(
                "Application is not installed on this server"
            )

        # Object-level authorization at the core boundary. ``user_id`` used to
        # be accepted and then ignored, so any internal caller of this public
        # API could uninstall an application from a server it neither installed
        # onto nor manages. Only the original installer or a caller holding
        # server.manage may remove an installation. When no servers module is
        # wired the permission half cannot be evaluated, so a non-installer is
        # denied (fail closed) rather than being granted every server.
        if isinstance(installation, dict):
            installer_id = installation.get("installer_id")
        else:
            installer_id = installation[1] if len(installation) > 1 else None
        is_installer = installer_id is not None and int(installer_id) == int(user_id)
        can_manage = bool(
            self._servers
            and self._servers.has_permission(user_id, server_id, "server.manage")
        )
        if not (is_installer or can_manage):
            raise PermissionDeniedError(
                "Missing required permission to uninstall this application",
                "server.manage",
            )

        self._db.execute(
            "DELETE FROM app_installations WHERE application_id = ? AND server_id = ?",
            (application_id, server_id),
        )

        app = self.get_application(application_id)
        if app and app.bot_id and self._servers:
            try:
                self._servers.remove_member(app.bot_id, server_id)
            except Exception as e:
                logger.warning(f"Failed to remove bot from server: {e}")

        logger.info(f"Application {application_id} uninstalled from server {server_id}")
        return True

    def get_installations(
        self,
        application_id: Optional[SnowflakeID] = None,
        server_id: Optional[SnowflakeID] = None,
    ) -> List[ApplicationInstallation]:
        if application_id and server_id:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations
                   WHERE application_id = ? AND server_id = ?""",
                (application_id, server_id),
            )
        elif application_id:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations WHERE application_id = ?""",
                (application_id,),
            )
        elif server_id:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations WHERE server_id = ?""",
                (server_id,),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations"""
            )

        return [row_to_installation(row) for row in rows]
