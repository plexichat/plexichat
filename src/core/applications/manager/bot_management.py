import json
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import SnowflakeID

from ..models import (
    ApprovedBot,
    BotRequest,
    BotProfile,
    BotApprovalStatus,
    UserAuthorizedApplication,
)
from ..exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    BotLimitError,
    BotRequestError,
    BotRequestExistsError,
    BotAlreadyApprovedError,
    LicenseFeatureError,
    PermissionDeniedError,
    InstallationExistsError,
    InstallationNotFoundError,
)
from .row_mappers import row_to_approved_bot, row_to_bot_request, row_to_bot_profile
from .protocol import ApplicationManagerProtocol


class BotManagementMixin(ApplicationManagerProtocol):
    def _require_server_manage_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> None:
        if not self._servers:
            raise PermissionDeniedError("Servers module not available", "server.manage")
        if not self._servers.has_permission(user_id, server_id, "server.manage"):
            raise PermissionDeniedError(
                "Missing required permission: server.manage", "server.manage"
            )

    def approve_bot(
        self,
        server_id: SnowflakeID,
        application_id: SnowflakeID,
        approved_by: SnowflakeID,
        permissions: str = "0",
        bot_name: Optional[str] = None,
    ) -> ApprovedBot:
        self._check_bot_license()
        self._require_server_manage_permission(approved_by, server_id)

        existing = self._db.fetch_one(
            "SELECT id FROM app_approved_bots WHERE server_id = ? AND application_id = ? AND status = 'approved'",
            (server_id, application_id),
        )
        if existing:
            raise BotAlreadyApprovedError("Bot is already approved on this server")

        bot_config = config.get("bots", {})
        max_bots = bot_config.get("max_per_server", 10)

        if self._check_premium_license():
            max_bots = bot_config.get("max_per_server_premium", 50)

        current_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM app_approved_bots WHERE server_id = ? AND status = 'approved'",
            (server_id,),
        )
        current = current_count["count"] if current_count else 0

        if current >= max_bots:
            raise BotLimitError(
                f"Maximum of {max_bots} approved bots per server", max_bots, current
            )

        bot_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO app_approved_bots
               (id, server_id, application_id, approved_by, permissions, bot_name,
                bot_avatar_url, status, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bot_id,
                server_id,
                application_id,
                approved_by,
                permissions,
                bot_name,
                None,
                "approved",
                now,
                now,
            ),
        )

        try:
            self.install_application(
                application_id, server_id, approved_by, permissions
            )
        except InstallationExistsError:
            pass
        except Exception as e:
            logger.warning(f"Failed to auto-install bot on server: {e}")

        logger.info(
            f"Bot {application_id} approved on server {server_id} by {approved_by}"
        )

        return ApprovedBot(
            id=bot_id,
            server_id=server_id,
            application_id=application_id,
            approved_by=approved_by,
            permissions=permissions,
            bot_name=bot_name,
            bot_avatar_url=None,
            status=BotApprovalStatus.APPROVED,
            installed_at=now,
            updated_at=now,
        )

    def remove_approved_bot(
        self,
        server_id: SnowflakeID,
        application_id: SnowflakeID,
        user_id: SnowflakeID,
    ) -> bool:
        self._require_server_manage_permission(user_id, server_id)
        now = self._get_timestamp()

        self._db.execute(
            "UPDATE app_approved_bots SET status = 'removed', updated_at = ? WHERE server_id = ? AND application_id = ?",
            (now, server_id, application_id),
        )

        try:
            self.uninstall_application(application_id, server_id, user_id)
        except InstallationNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to uninstall bot on removal: {e}")

        logger.info(f"Bot {application_id} removed from server {server_id}")
        return True

    def get_approved_bots(
        self,
        server_id: Optional[SnowflakeID] = None,
        application_id: Optional[SnowflakeID] = None,
        status: Optional[str] = None,
    ) -> List[ApprovedBot]:
        conditions = []
        params = []

        if server_id:
            conditions.append("ab.server_id = ?")
            params.append(server_id)
        if application_id:
            conditions.append("ab.application_id = ?")
            params.append(application_id)
        if status:
            conditions.append("ab.status = ?")
            params.append(status)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        rows = self._db.fetch_all(
            f"""SELECT ab.id, ab.server_id, ab.application_id, ab.approved_by,
                      ab.permissions, ab.bot_name, ab.bot_avatar_url, ab.status,
                      ab.installed_at, ab.updated_at,
                      a.name as app_name, a.icon_url as app_icon, a.bot_id
               FROM app_approved_bots ab
               LEFT JOIN app_applications a ON ab.application_id = a.id{where_clause}
               ORDER BY ab.installed_at DESC""",
            tuple(params),
        )

        return [row_to_approved_bot(row) for row in rows]

    def request_bot(
        self,
        server_id: SnowflakeID,
        application_id: SnowflakeID,
        requester_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> BotRequest:
        existing = self._db.fetch_one(
            "SELECT id FROM app_bot_requests WHERE server_id = ? AND application_id = ? AND status = 'pending'",
            (server_id, application_id),
        )
        if existing:
            raise BotRequestExistsError("A pending request for this bot already exists")

        already_approved = self._db.fetch_one(
            "SELECT id FROM app_approved_bots WHERE server_id = ? AND application_id = ? AND status = 'approved'",
            (server_id, application_id),
        )
        if already_approved:
            raise BotAlreadyApprovedError("Bot is already approved on this server")

        request_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO app_bot_requests
               (id, server_id, application_id, requester_id, reason, status,
                reviewed_by, review_reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)""",
            (request_id, server_id, application_id, requester_id, reason, now, now),
        )

        logger.info(
            f"Bot request {request_id} for app {application_id} on server {server_id}"
        )

        return BotRequest(
            id=request_id,
            server_id=server_id,
            application_id=application_id,
            requester_id=requester_id,
            reason=reason,
            status=BotApprovalStatus.PENDING,
            reviewed_by=None,
            review_reason=None,
            created_at=now,
            updated_at=now,
        )

    def review_bot_request(
        self,
        server_id: Optional[SnowflakeID],
        request_id: SnowflakeID,
        reviewer_id: SnowflakeID,
        approve: bool,
        review_reason: Optional[str] = None,
    ) -> BotRequest:
        row = self._db.fetch_one(
            "SELECT * FROM app_bot_requests WHERE id = ?",
            (request_id,),
        )
        if not row:
            raise BotRequestError("Bot request not found", request_id)

        if server_id is not None and row["server_id"] != server_id:
            raise BotRequestError("Bot request not found", request_id)

        self._require_server_manage_permission(reviewer_id, row["server_id"])

        status = "approved" if approve else "denied"
        now = self._get_timestamp()

        self._db.execute(
            """UPDATE app_bot_requests SET status = ?, reviewed_by = ?, review_reason = ?,
               updated_at = ? WHERE id = ?""",
            (status, reviewer_id, review_reason, now, request_id),
        )

        if approve:
            try:
                self.approve_bot(
                    server_id=row["server_id"],
                    application_id=row["application_id"],
                    approved_by=reviewer_id,
                )
            except BotAlreadyApprovedError:
                pass
            except Exception as e:
                logger.warning(f"Failed to auto-approve bot after request review: {e}")

        logger.info(f"Bot request {request_id} {status} by {reviewer_id}")

        return BotRequest(
            id=row["id"],
            server_id=row["server_id"],
            application_id=row["application_id"],
            requester_id=row["requester_id"],
            reason=row["reason"],
            status=BotApprovalStatus(status),
            reviewed_by=reviewer_id,
            review_reason=review_reason,
            created_at=row["created_at"],
            updated_at=now,
        )

    def get_bot_requests(
        self,
        server_id: Optional[SnowflakeID] = None,
        requester_id: Optional[SnowflakeID] = None,
        status: Optional[str] = None,
    ) -> List[BotRequest]:
        conditions = []
        params = []

        if server_id:
            conditions.append("server_id = ?")
            params.append(server_id)
        if requester_id:
            conditions.append("requester_id = ?")
            params.append(requester_id)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        rows = self._db.fetch_all(
            f"""SELECT id, server_id, application_id, requester_id, reason, status,
                      reviewed_by, review_reason, created_at, updated_at
               FROM app_bot_requests{where_clause}
               ORDER BY created_at DESC""",
            tuple(params),
        )

        return [row_to_bot_request(row) for row in rows]

    def get_bot_profile(self, application_id: SnowflakeID) -> Optional[BotProfile]:
        row = self._db.fetch_one(
            """SELECT application_id, description, short_description, avatar_url, banner_url,
                      website_url, support_url, github_url, tags, nsfw, private, updated_at
               FROM app_bot_profiles WHERE application_id = ?""",
            (application_id,),
        )

        if not row:
            return None

        return row_to_bot_profile(row)

    def update_bot_profile(
        self,
        application_id: SnowflakeID,
        user_id: SnowflakeID,
        description: Optional[str] = None,
        short_description: Optional[str] = None,
        avatar_url: Optional[str] = None,
        banner_url: Optional[str] = None,
        website_url: Optional[str] = None,
        support_url: Optional[str] = None,
        github_url: Optional[str] = None,
        tags: Optional[List[str]] = None,
        nsfw: Optional[bool] = None,
        private: Optional[bool] = None,
    ) -> BotProfile:
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")
        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        existing = self.get_bot_profile(application_id)
        now = self._get_timestamp()

        if existing:
            updates = []
            params = []
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if short_description is not None:
                updates.append("short_description = ?")
                params.append(short_description)
            if avatar_url is not None:
                updates.append("avatar_url = ?")
                params.append(avatar_url)
            if banner_url is not None:
                updates.append("banner_url = ?")
                params.append(banner_url)
            if website_url is not None:
                updates.append("website_url = ?")
                params.append(website_url)
            if support_url is not None:
                updates.append("support_url = ?")
                params.append(support_url)
            if github_url is not None:
                updates.append("github_url = ?")
                params.append(github_url)
            if tags is not None:
                updates.append("tags = ?")
                params.append(json.dumps(tags))
            if nsfw is not None:
                updates.append("nsfw = ?")
                params.append(1 if nsfw else 0)
            if private is not None:
                updates.append("private = ?")
                params.append(1 if private else 0)

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(application_id)
                self._db.execute(
                    f"UPDATE app_bot_profiles SET {', '.join(updates)} WHERE application_id = ?",
                    tuple(params),
                )
        else:
            self._db.execute(
                """INSERT INTO app_bot_profiles
                   (application_id, description, short_description, avatar_url, banner_url,
                    website_url, support_url, github_url, tags, nsfw, private, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    application_id,
                    description,
                    short_description,
                    avatar_url,
                    banner_url,
                    website_url,
                    support_url,
                    github_url,
                    json.dumps(tags or []),
                    1 if nsfw else 0,
                    1 if private else 0,
                    now,
                ),
            )

        logger.info(f"Bot profile updated for application {application_id}")

        result = self.get_bot_profile(application_id)
        assert result is not None
        return result

    def get_user_authorized_apps(
        self, user_id: SnowflakeID
    ) -> List[UserAuthorizedApplication]:
        rows = self._db.fetch_all(
            """SELECT t.id, t.application_id, t.scopes, t.created_at, t.expires_at,
                      a.name as app_name, a.icon_url as app_icon
               FROM app_oauth_tokens t
               JOIN app_applications a ON t.application_id = a.id
               WHERE t.user_id = ? AND t.revoked = 0
               ORDER BY t.created_at DESC""",
            (user_id,),
        )

        return [
            UserAuthorizedApplication(
                id=row["id"],
                application_id=row["application_id"],
                application_name=row["app_name"],
                application_icon=row["app_icon"],
                scopes=json.loads(row["scopes"]) if row["scopes"] else [],
                authorized_at=row["created_at"],
                last_used_at=row["expires_at"],
            )
            for row in rows
        ]

    def revoke_authorized_app(
        self, token_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        self._db.execute(
            "UPDATE app_oauth2_tokens SET revoked = 1 WHERE id = ? AND user_id = ?",
            (token_id, user_id),
        )
        logger.info(f"Authorized app token {token_id} revoked by user {user_id}")
        return True

    def get_bot_directory(
        self,
        server_id: Optional[SnowflakeID] = None,
        include_public: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        query_parts = [
            """SELECT a.id, a.name, a.description, a.icon_url, a.bot_id,
                      bp.short_description, bp.tags, bp.nsfw, bp.private
               FROM app_applications a
               LEFT JOIN app_bot_profiles bp ON a.id = bp.application_id
               WHERE a.bot_id IS NOT NULL"""
        ]
        params = []

        if include_public:
            query_parts.append("AND (bp.private IS NULL OR bp.private = 0)")

        if server_id:
            query_parts.append(
                "AND a.id NOT IN (SELECT application_id FROM app_approved_bots WHERE server_id = ? AND status = 'approved')"
            )
            params.append(server_id)

        query_parts.append("ORDER BY a.name ASC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        rows = self._db.fetch_all(" ".join(query_parts), tuple(params))

        result = []
        for row in rows:
            tags = json.loads(row["tags"]) if row["tags"] else []
            result.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["short_description"] or row["description"],
                    "icon_url": row["icon_url"],
                    "bot_id": row["bot_id"],
                    "tags": tags,
                    "nsfw": bool(row["nsfw"]) if row["nsfw"] else False,
                }
            )

        return result

    def _check_bot_license(self) -> bool:
        try:
            import importlib

            license_module = importlib.import_module("utils.licensing")
            if hasattr(license_module, "setup"):
                if not license_module.has_feature("bots", default=False):
                    raise LicenseFeatureError(
                        "Bots feature requires a valid license", "bots"
                    )
            return True
        except ImportError:
            logger.warning("Licensing module not available, bots feature allowed")
            return True
        except Exception as e:
            logger.warning(f"License check failed for bots feature: {e}")
            return True

    def _check_premium_license(self) -> bool:
        try:
            import importlib

            license_module = importlib.import_module("utils.licensing")
            if hasattr(license_module, "has_feature"):
                return license_module.has_feature("premium", default=False)
            return False
        except ImportError:
            return False
        except Exception:
            return False
