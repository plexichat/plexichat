import json
import threading
import weakref
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
from ..validation import (
    MAX_APP_SHORT_DESCRIPTION_LENGTH,
    validate_application_description,
    validate_bot_display_name,
    validate_bot_reason,
    validate_bot_tags,
    validate_homepage_url,
)
from .row_mappers import row_to_approved_bot, row_to_bot_request, row_to_bot_profile
from .protocol import ApplicationManagerProtocol
from .lookup_cache import CACHE_MISS, BoundedTTLCache


# Bot directory pages are clamped on both sides. ``limit`` used to reach SQL
# ``LIMIT ?`` with only the route's upper bound, and SQLite reads ``LIMIT -1``
# as *unlimited* -- so a negative limit dumped every public bot instead of an
# empty page. The route clamps too, but the manager backs internal callers.
MAX_DIRECTORY_PAGE_SIZE = 100


def _escape_like(text: str) -> str:
    """Escape LIKE metacharacters so a directory filter matches literally.

    ``q`` and ``tag`` are caller-supplied (HTTP query params). Parameterizing
    the value blocks SQL injection but not ``%``/``_`` acting as wildcards: a
    bare ``%`` search silently matched every bot, and a crafted ``tag`` could
    widen the filter to enumerate rows outside the requested tag. Escape the
    three metacharacters and declare ``ESCAPE '\\'`` on every clause so these
    filters mean exactly what the caller typed.
    """
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# The directory is read far more often than it changes, and each read ran a
# COUNT query plus a page query with LIKE/tag filters. Keep a small, short-lived
# cache of fully built slices so repeated directory requests skip both the SQL
# round trips and the per-row JSON decoding. Writes that can change what is
# visible in a directory call ``_invalidate_bot_directory_cache`` so a change is
# reflected on the very next read. The TTL is only a guard for writes performed
# through another process / a raw DB handle that bypasses these hooks.
#
# SECURITY: the cache is owned by the manager instance, never by the module. A
# module-global slice cache is shared by every ApplicationManager in the
# process, so a manager bound to one DB can be served a slice built from
# another manager's DB (cross-tenant metadata bleed). Per-manager ownership
# keeps cached rows scoped to the connection that produced them. Bounds and TTL
# reuse the proven ``BoundedTTLCache`` so the slice cache cannot grow without
# limit and stale rows age out even if a raw DB write skips the hooks.
_DIRECTORY_CACHE_TTL_SECONDS = 5.0
_DIRECTORY_CACHE_MAX_ENTRIES = 64
_DIRECTORY_CACHE_MAX_BYTES = 256 * 1024
_DIRECTORY_CACHE_INIT_LOCK = threading.Lock()

# Managers register here the first time they build a directory slice. This registry
# exists only for the module-level ``invalidate_bot_directory_cache`` compatibility
# shim (tests / raw-DB helpers with no manager handle); production write paths
# invalidate their own manager directly via ``_invalidate_bot_directory_cache``.
_live_directory_managers: "weakref.WeakSet[BotManagementMixin]" = weakref.WeakSet()


def _directory_slice_size(value: Dict[str, Any]) -> int:
    """Approximate the retained bytes of a cached directory slice."""
    total = 64  # wrapper dict / total field overhead
    for bot in value.get("bot_list", ()):
        for key, item in bot.items():
            total += len(str(key).encode("utf-8")) + len(str(item).encode("utf-8"))
    return max(1, total)


def invalidate_bot_directory_cache() -> None:
    """Compatibility shim: drop cached slices for every live manager.

    Production writes use the per-manager ``_invalidate_bot_directory_cache``
    instead; this broadcast form exists for callers holding no manager handle
    that still need to force a refresh (tests, raw-DB maintenance helpers).
    """
    for manager in list(_live_directory_managers):
        manager._invalidate_bot_directory_cache()


class BotManagementMixin(ApplicationManagerProtocol):
    def _get_directory_cache(self) -> BoundedTTLCache:
        """Return this manager's directory-slice cache, creating it on first use.

        The cache lives on the manager (not at module scope) so two managers —
        and two DB connections — never share directory rows. Creation is guarded
        so concurrent first reads on one manager get the same instance.
        """
        cache = getattr(self, "_bot_directory_cache", None)
        if cache is None:
            with _DIRECTORY_CACHE_INIT_LOCK:
                cache = getattr(self, "_bot_directory_cache", None)
                if cache is None:
                    cache = BoundedTTLCache(
                        max_entries=_DIRECTORY_CACHE_MAX_ENTRIES,
                        max_bytes=_DIRECTORY_CACHE_MAX_BYTES,
                        ttl_seconds=_DIRECTORY_CACHE_TTL_SECONDS,
                        sizer=_directory_slice_size,
                    )
                    self._bot_directory_cache = cache
                _live_directory_managers.add(self)
        return cache

    def _invalidate_bot_directory_cache(self) -> None:
        """Drop this manager's cached directory slices after a write.

        Only an existing cache is touched — an invalidation on a manager that
        never read the directory must not allocate a cache just to clear it.
        """
        cache = getattr(self, "_bot_directory_cache", None)
        if cache is not None:
            cache.invalidate()

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
        permissions: str = "{}",
        bot_name: Optional[str] = None,
        *,
        _permission_checked: bool = False,
    ) -> ApprovedBot:
        # SECURITY: authorize before licensing so a user without
        # ``server.manage`` gets PermissionDeniedError rather than a
        # license error (which would leak feature availability). Internal
        # callers that already performed this exact check for the same
        # user/server (review_bot_request) may skip the duplicate lookup.
        if not _permission_checked:
            self._require_server_manage_permission(approved_by, server_id)
        self._check_bot_license()

        # Bound free-form bot metadata before it reaches storage/directory.
        bot_name = validate_bot_display_name(bot_name)

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
        self._invalidate_bot_directory_cache()

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

        # SECURITY: only transition rows that are in an active state
        # (approved or pending).  Previously the UPDATE targeted ALL rows
        # regardless of current status, which would silently flip
        # previously-denied requests to 'removed' — erasing audit
        # history and allowing re-approval via the request-bot flow.
        cursor = self._db.execute(
            "UPDATE app_approved_bots SET status = 'removed', updated_at = ? "
            "WHERE server_id = ? AND application_id = ? AND status IN ('approved', 'pending')",
            (now, server_id, application_id),
        )
        rows = getattr(cursor, "rowcount", 0)
        if rows == 0:
            raise InstallationNotFoundError(
                f"No active bot installation found for app {application_id} on server {server_id}"
            )

        try:
            self.uninstall_application(application_id, server_id, user_id)
        except InstallationNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to uninstall bot on removal: {e}")

        logger.info(
            f"Bot {application_id} removed from server {server_id} "
            f"({rows} row(s) transitioned)"
        )
        self._invalidate_bot_directory_cache()
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

        # Only approved-bot columns are consumed by callers (the response's
        # app metadata fields are optional and never populated here). Reading
        # them through a LEFT JOIN on app_applications added a join to every
        # approved-bot listing for columns that were immediately discarded.
        rows = self._db.fetch_all(
            f"""SELECT ab.id, ab.server_id, ab.application_id, ab.approved_by,
                      ab.permissions, ab.bot_name, ab.bot_avatar_url, ab.status,
                      ab.installed_at, ab.updated_at
               FROM app_approved_bots ab{where_clause}
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
        # Bound free-form request text before it is persisted/returned.
        reason = validate_bot_reason(reason)
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

        import utils.config as cfg

        bot_config = cfg.get("bots", {})
        max_pending = bot_config.get("max_requests_pending_per_user", 5)
        pending_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM app_bot_requests WHERE requester_id = ? AND status = 'pending'",
            (requester_id,),
        )
        current_pending = pending_count["count"] if pending_count else 0
        if current_pending >= max_pending:
            raise BotRequestError(
                f"Maximum of {max_pending} pending bot requests per user",
                request_id=None,
            )

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
        server_id: SnowflakeID,
        request_id: SnowflakeID,
        reviewer_id: SnowflakeID,
        approve: bool,
        review_reason: Optional[str] = None,
    ) -> BotRequest:
        # Bound free-form review text before it is persisted/returned.
        review_reason = validate_bot_reason(review_reason, field="review_reason")
        row = self._db.fetch_one(
            "SELECT * FROM app_bot_requests WHERE id = ?",
            (request_id,),
        )
        if not row:
            raise BotRequestError("Bot request not found", request_id)

        if row["server_id"] != server_id:
            raise BotRequestError("Bot request not found", request_id)

        if row["status"] != "pending":
            raise BotRequestError(f"Request already {row['status']}", request_id)

        # SECURITY: prevent self-review.  A requester must not approve or
        # deny their own bot request — that would bypass the second-party
        # review that the approval flow is designed to enforce.
        if row["requester_id"] == reviewer_id:
            raise PermissionDeniedError(
                "Requesters cannot review their own bot request",
                "bots.review",
            )

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
                    # review_bot_request already verified server.manage for
                    # this reviewer/server pair above; don't repeat it.
                    _permission_checked=True,
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

    def get_bot_profile(
        self, application_id: SnowflakeID, viewer_id: Optional[SnowflakeID] = None
    ) -> Optional[BotProfile]:
        """Get a bot's profile.

        A profile marked private is only returned to the application owner
        (``viewer_id``). The directory already filters private entries, so
        answering a direct lookup without this check would leak them anyway.
        Returns ``None`` for a hidden profile exactly as for a missing one, so
        the caller cannot distinguish "private" from "does not exist".
        """
        # Public profiles are the common case and need no application row at
        # all. Only a private profile requires the owner_id comparison, so
        # fetch that metadata lazily instead of joining app_applications on
        # every profile read.
        row = self._db.fetch_one(
            """SELECT application_id, description, short_description,
                      avatar_url, banner_url, website_url, support_url,
                      github_url, tags, nsfw, private, updated_at
               FROM app_bot_profiles
               WHERE application_id = ?""",
            (application_id,),
        )

        if not row:
            return None

        if row["private"]:
            owner = self._db.fetch_one(
                "SELECT owner_id FROM app_applications WHERE id = ?",
                (application_id,),
            )
            if owner is None or viewer_id is None:
                return None
            # SECURITY: only a genuine integer id from the authoritative
            # app_applications.owner_id row may attest to ownership. int()
            # laundering of a malformed value ("05"->5, 5.9->5, True->1) would
            # disclose a private bot profile to a non-owner viewer. type()
            # also rejects bool.
            app_owner_id = owner["owner_id"]
            if type(app_owner_id) is not int or app_owner_id != int(viewer_id):
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

        description = validate_application_description(description)
        short_description = validate_application_description(
            short_description,
            field="short_description",
            max_length=MAX_APP_SHORT_DESCRIPTION_LENGTH,
        )
        website_url = validate_homepage_url(website_url, field="website_url")
        support_url = validate_homepage_url(support_url, field="support_url")
        github_url = validate_homepage_url(github_url, field="github_url")
        # Only validate when supplied: an omitted field must stay "omitted" so
        # a partial update does not silently reset an existing tag list.
        if tags is not None:
            tags = validate_bot_tags(tags)

        existing = self.get_bot_profile(application_id, viewer_id=user_id)
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
        # Profile text/tags/visibility are part of directory entries.
        self._invalidate_bot_directory_cache()

        result = self.get_bot_profile(application_id, viewer_id=user_id)
        if result is None:
            raise ApplicationNotFoundError("Bot profile not found after save")
        return result

    def get_user_authorized_apps(
        self, user_id: SnowflakeID
    ) -> List[UserAuthorizedApplication]:
        # M12: expired or revoked tokens must never surface as authorized.
        # The SQL WHERE clause is the primary filter; the Python loop below
        # is defense-in-depth so a row that slips past the query (quirky
        # driver, clock skew between layers) can never leak an expired or
        # revoked grant into the authorized list.
        now_ms = self._get_timestamp()
        rows = self._db.fetch_all(
            """SELECT t.id, t.application_id, t.scopes, t.created_at, t.expires_at,
                      t.revoked, t.last_used_at,
                      a.name as app_name, a.icon_url as app_icon
               FROM app_oauth_tokens t
               JOIN app_applications a ON t.application_id = a.id
               WHERE t.user_id = ? AND t.revoked = 0 AND t.expires_at > ?
               ORDER BY t.created_at DESC""",
            (user_id, now_ms),
        )

        apps = []
        for row in rows:
            try:
                revoked = int(row["revoked"]) if row["revoked"] is not None else 0
            except (TypeError, ValueError):
                continue  # fail closed: unreadable flag -> not authorized
            try:
                expires_at = int(row["expires_at"])
            except (TypeError, ValueError):
                continue  # fail closed: no parseable expiry -> not authorized
            if revoked or expires_at <= now_ms:
                continue
            apps.append(
                UserAuthorizedApplication(
                    id=row["id"],
                    application_id=row["application_id"],
                    application_name=row["app_name"],
                    application_icon=row["app_icon"],
                    scopes=json.loads(row["scopes"]) if row["scopes"] else [],
                    authorized_at=row["created_at"],
                    last_used_at=row["last_used_at"],
                )
            )
        return apps

    def revoke_authorized_app(
        self, token_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        # SECURITY/CORRECTNESS FIX: the prior implementation targeted a
        # non-existent table ``app_oauth2_tokens`` so the revoke call
        # silently mutated zero rows. The real revocation table is
        # ``app_oauth_tokens`` (matches ``get_user_authorized_apps``
        # above and the DSAR collector). The query is portable
        # across SQLite / Postgres because it avoids ``RETURNING``
        # and ``DELETE … WHERE id=`` semantics that differ across
        # drivers.
        cursor = self._db.execute(
            "UPDATE app_oauth_tokens SET revoked = 1 WHERE id = ? AND user_id = ?",
            (token_id, user_id),
        )
        try:
            rows = cursor.rowcount
        except Exception:
            rows = None  # pragma: no cover -- drivers without rowcount
        try:
            cursor.close()
        except Exception as close_error:
            logger.debug(f"bot management cursor close failed: {close_error}")
        logger.info(
            f"Authorized app token {token_id} revoked by user {user_id} "
            f"(rows affected: {rows if rows is not None else 'unknown'})"
        )
        if rows is not None and rows == 0:
            # Surface this as a hard failure so the API layer can
            # return 404 instead of accepting a no-op revocation.
            from ..exceptions import ApplicationNotFoundError

            raise ApplicationNotFoundError(
                f"No authorized app token {token_id} for user {user_id}"
            )
        return True

    def get_bot_directory(
        self,
        server_id: Optional[SnowflakeID] = None,
        include_public: bool = True,
        limit: int = 50,
        offset: int = 0,
        q: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        limit = max(0, min(limit, MAX_DIRECTORY_PAGE_SIZE))
        offset = max(0, offset)

        # Cache key uses the normalized arguments so equivalent calls share a
        # slice regardless of how the caller spelled the (nullable) filters.
        # ``include_public`` is part of the key so a slice that includes private
        # bots can never be served to the public directory route.
        cache_key = (server_id, bool(include_public), limit, offset, q, tag)
        cache = self._get_directory_cache()
        cached = cache.get(cache_key)
        if cached is not CACHE_MISS:
            # Hand out a fresh container so callers cannot mutate the snapshot.
            return {
                "bot_list": [dict(bot) for bot in cached["bot_list"]],
                "total": cached["total"],
            }

        base_where = ["a.bot_id IS NOT NULL"]
        params = []

        if include_public:
            base_where.append("(bp.private IS NULL OR bp.private = 0)")

        if server_id:
            base_where.append(
                "a.id NOT IN (SELECT application_id FROM app_approved_bots WHERE server_id = ? AND status = 'approved')"
            )
            params.append(server_id)

        if q:
            base_where.append(
                "(a.name LIKE ? ESCAPE '\\' OR bp.short_description LIKE ? ESCAPE '\\')"
            )
            like_q = f"%{_escape_like(q)}%"
            params.append(like_q)
            params.append(like_q)

        if tag:
            base_where.append("bp.tags LIKE ? ESCAPE '\\'")
            params.append(f'%"{_escape_like(tag)}"%')

        where_clause = " AND ".join(base_where)

        count_row = self._db.fetch_one(
            f"""SELECT COUNT(*) as total
                FROM app_applications a
                LEFT JOIN app_bot_profiles bp ON a.id = bp.application_id
                WHERE {where_clause}""",
            tuple(params),
        )
        total = count_row["total"] if count_row else 0

        rows = self._db.fetch_all(
            f"""SELECT a.id, a.name, a.description, a.icon_url, a.bot_id,
                       bp.short_description, bp.tags, bp.nsfw, bp.private
                FROM app_applications a
                LEFT JOIN app_bot_profiles bp ON a.id = bp.application_id
                WHERE {where_clause}
                ORDER BY a.name ASC
                LIMIT ? OFFSET ?""",
            tuple(params + [limit, offset]),
        )

        bots = []
        for row in rows:
            tags = json.loads(row["tags"]) if row["tags"] else []
            bots.append(
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

        result = {"bot_list": bots, "total": total}
        # Store a snapshot so the caller's result is not aliased into the cache.
        cache.put(
            cache_key,
            {
                "bot_list": [dict(bot) for bot in bots],
                "total": total,
            },
        )
        return result

    def _check_bot_license(self) -> bool:
        try:
            import importlib

            license_module = importlib.import_module("utils.licensing")
            if not license_module.has_feature("bots", default=False):
                raise LicenseFeatureError(
                    "Bots feature requires a valid license", "bots"
                )
            return True
        except ImportError as exc:
            # Fail closed: an unavailable licensing module means the bots
            # feature gate cannot be evaluated. Allowing the approval here
            # would silently bypass the license check entirely.
            raise LicenseFeatureError(
                "Bots feature requires a valid license", "bots"
            ) from exc
        except LicenseFeatureError:
            raise

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
