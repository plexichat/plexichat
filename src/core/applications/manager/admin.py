from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from .protocol import ApplicationManagerProtocol


# Every admin listing helper in this module clamps its page bound to this
# ceiling. ``limit`` used to reach SQL ``LIMIT ?`` with only an upper bound,
# and SQLite reads ``LIMIT -1`` as *unlimited* -- so
# ``GET /admin/bots/applications?limit=-1`` returned the whole table instead
# of an empty page. Clamping from below fixes that; clamping from above keeps
# a single request from materializing an unbounded result set.
MAX_ADMIN_PAGE_SIZE = 100


# Scalar counters produced by the single batched dashboard feature-stats
# query in ``get_admin_dashboard_feature_stats``. ``report_categories`` is a
# grouped list and is fetched separately.
_FEATURE_COUNT_COLUMNS = (
    "bookmarks",
    "scheduled_messages_pending",
    "forwarded_messages",
    "voice_messages",
    "profiles_with_status",
    "push_tokens",
    "webhook_retries_pending",
    "webhook_retries_retrying",
    "webhook_retries_dead_letter",
    "dm_spam_filters_active",
    "threads_with_slowmode",
)


@dataclass
class AdminBotStats:
    total_applications: int = 0
    total_bots: int = 0
    total_approved: int = 0
    total_pending_requests: int = 0
    total_installations: int = 0
    servers_with_bots: int = 0
    recent_approvals: int = 0
    recent_requests: int = 0


class AdminMixin(ApplicationManagerProtocol):
    def _count_rows(self, table: str, where: str = "", params: tuple = ()) -> int:
        if not self._db.table_exists(table):
            return 0
        query = f"SELECT COUNT(*) as count FROM {table}"
        if where:
            query = f"{query} WHERE {where}"
        row = self._db.fetch_one(query, params)
        return int(row["count"]) if row else 0

    def _maybe_count_rows(self, query: str, params: tuple = ()) -> int:
        row = self._db.fetch_one(query, params)
        return int(row["count"]) if row else 0

    def _count_rows_any(
        self, tables: List[str], where: str = "", params: tuple = ()
    ) -> int:
        for table in tables:
            if self._db.table_exists(table):
                return self._count_rows(table, where, params)
        return 0

    def _table_count_query(
        self, table: str, where: str = "", params: tuple = ()
    ) -> int:
        if not self._db.table_exists(table):
            return 0
        return self._maybe_count_rows(
            f"SELECT COUNT(*) as count FROM {table}"
            + (f" WHERE {where}" if where else ""),
            params,
        )

    def get_admin_bot_stats(self) -> Dict[str, int]:
        week_ago = self._get_timestamp() - 7 * 24 * 60 * 60 * 1000
        return {
            "total_applications": self._count_rows("app_applications"),
            "total_bots": self._count_rows("app_applications", "bot_id IS NOT NULL"),
            "total_approved": self._count_rows(
                "app_approved_bots", "status = 'approved'"
            ),
            "total_pending_requests": self._count_rows(
                "app_bot_requests", "status = 'pending'"
            ),
            "total_installations": self._count_rows("app_installations"),
            "servers_with_bots": self._maybe_count_rows(
                "SELECT COUNT(DISTINCT server_id) as count FROM app_approved_bots WHERE status = 'approved'"
            ),
            "recent_approvals": self._count_rows(
                "app_approved_bots", "installed_at >= ?", (week_ago,)
            ),
            "recent_requests": self._count_rows(
                "app_bot_requests", "created_at >= ?", (week_ago,)
            ),
        }

    def get_admin_dashboard_feature_stats(self) -> Dict[str, Any]:
        """Build the admin dashboard's feature counters.

        The cold dashboard build used to issue one COUNT query (plus a
        table-existence probe) per feature. All of the scalar counters are
        now folded into a single batched query: each count is a derived table
        LEFT JOINed onto a one-row seed, and the three webhook-retry states
        come from one conditional-aggregation pass over the queue table. A
        missing table contributes a literal ``0`` column, so partial schemas
        and the two legacy alias tables still behave exactly as before.
        """
        # Legacy alias tables: first present wins, matching _count_rows_any.
        scheduled_table = next(
            (
                table
                for table in ("msg_scheduled", "scheduled_messages")
                if self._db.table_exists(table)
            ),
            None,
        )
        forwarded_table = next(
            (
                table
                for table in ("msg_forwarded", "forwarded_messages")
                if self._db.table_exists(table)
            ),
            None,
        )

        core_tables = (
            "user_bookmarks",
            "msg_messages",
            "user_profiles",
            "push_tokens",
            "webhook_retry_queue",
            "message_reports",
            "dm_spam_filters",
            "thread_threads",
        )
        present = {table: self._db.table_exists(table) for table in core_tables}

        projections: List[str] = []
        joins: List[str] = []

        def add_count(column: str, table: Optional[str], where: str = "") -> None:
            if table is None:
                projections.append(f"0 AS {column}")
                return
            sql = f"SELECT COUNT(*) AS c FROM {table}"
            if where:
                sql = f"{sql} WHERE {where}"
            joins.append(f"LEFT JOIN ({sql}) AS c_{column} ON 1=1")
            projections.append(f"COALESCE(c_{column}.c, 0) AS {column}")

        add_count("bookmarks", "user_bookmarks" if present["user_bookmarks"] else None)
        add_count("scheduled_messages_pending", scheduled_table, "status = 'pending'")
        add_count("forwarded_messages", forwarded_table)
        add_count(
            "voice_messages",
            "msg_messages" if present["msg_messages"] else None,
            "message_type = 'voice'",
        )
        add_count(
            "profiles_with_status",
            "user_profiles" if present["user_profiles"] else None,
            "custom_status_text IS NOT NULL",
        )
        add_count("push_tokens", "push_tokens" if present["push_tokens"] else None)

        if present["webhook_retry_queue"]:
            joins.append(
                "LEFT JOIN (SELECT "
                "SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending, "
                "SUM(CASE WHEN status = 'retrying' THEN 1 ELSE 0 END) AS retrying, "
                "SUM(CASE WHEN status = 'dead_letter' THEN 1 ELSE 0 END) AS dead_letter "
                "FROM webhook_retry_queue) AS c_webhook ON 1=1"
            )
            projections.extend(
                (
                    "COALESCE(c_webhook.pending, 0) AS webhook_retries_pending",
                    "COALESCE(c_webhook.retrying, 0) AS webhook_retries_retrying",
                    "COALESCE(c_webhook.dead_letter, 0) AS webhook_retries_dead_letter",
                )
            )
        else:
            projections.extend(
                (
                    "0 AS webhook_retries_pending",
                    "0 AS webhook_retries_retrying",
                    "0 AS webhook_retries_dead_letter",
                )
            )

        add_count(
            "dm_spam_filters_active",
            "dm_spam_filters" if present["dm_spam_filters"] else None,
            "enabled = 1",
        )
        add_count(
            "threads_with_slowmode",
            "thread_threads" if present["thread_threads"] else None,
            "slowmode_interval_ms > 0",
        )

        query = (
            "SELECT "
            + ", ".join(projections)
            + " FROM (SELECT 1) AS seed "
            + " ".join(joins)
        )
        row = self._db.fetch_one(query) or {}
        feature_stats: Dict[str, Any] = {
            column: int(row.get(column) or 0) for column in _FEATURE_COUNT_COLUMNS
        }

        if present["message_reports"]:
            report_rows = self._db.fetch_all(
                "SELECT category, COUNT(*) as count FROM message_reports GROUP BY category ORDER BY count DESC LIMIT 10"
            )
            feature_stats["report_categories"] = [
                {"category": r["category"], "count": r["count"]} for r in report_rows
            ]
        else:
            feature_stats["report_categories"] = []
        return feature_stats

    def get_admin_dashboard_counts(self) -> Dict[str, Any]:
        total_users = self._count_rows("auth_users")
        active_users = self._count_rows(
            "auth_users", "last_login_at > ?", (int(self._get_timestamp() - 86400000),)
        )
        scheduled_deletions = self._count_rows(
            "auth_users", "deletion_status = 'frozen'"
        )
        return {
            "total_users": total_users,
            "active_users": active_users,
            "scheduled_deletions": scheduled_deletions,
            "db_status": "healthy",
        }

    def get_admin_bot_applications(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        limit = max(0, min(limit, MAX_ADMIN_PAGE_SIZE))
        offset = max(0, offset)

        rows = self._db.fetch_all(
            """SELECT a.id, a.name, a.owner_id, a.bot_id, a.icon_url, a.created_at,
                      (SELECT COUNT(*) FROM app_approved_bots ab
                       WHERE ab.application_id = a.id AND ab.status = 'approved') AS approved_count,
                      (SELECT COUNT(*) FROM app_bot_requests br
                       WHERE br.application_id = a.id AND br.status = 'pending') AS pending_count
               FROM app_applications a
               ORDER BY a.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "owner_id": row["owner_id"],
                "bot_id": (str(row["bot_id"]) if row["bot_id"] is not None else None),
                "icon_url": row["icon_url"],
                "approved_servers": row["approved_count"] or 0,
                "pending_requests": row["pending_count"] or 0,
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_admin_bot_requests(
        self,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        limit = max(0, min(limit, MAX_ADMIN_PAGE_SIZE))
        offset = max(0, offset)

        conditions = []
        params: List[Any] = []
        if status_filter:
            conditions.append("br.status = ?")
            params.append(status_filter)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        rows = self._db.fetch_all(
            f"""SELECT br.id, br.application_id, a.name as app_name, br.server_id,
                      br.requester_id, br.reason, br.status, br.created_at
               FROM app_bot_requests br
               JOIN app_applications a ON br.application_id = a.id{where_clause}
               ORDER BY br.created_at DESC
               LIMIT ? OFFSET ?""",
            tuple(params),
        )
        return [
            {
                "id": row["id"],
                "application_id": row["application_id"],
                "application_name": row["app_name"],
                "server_id": row["server_id"],
                "requester_id": row["requester_id"],
                "reason": row["reason"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
