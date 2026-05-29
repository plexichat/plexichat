from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from .protocol import ApplicationManagerProtocol


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
        try:
            row = self._db.fetch_one(query, params)
            return int(row["count"]) if row else 0
        except Exception:
            return 0

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
        week_ago = self._get_timestamp() - 604800
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
        feature_stats: Dict[str, Any] = {}
        feature_stats["bookmarks"] = self._count_rows("user_bookmarks")
        feature_stats["scheduled_messages_pending"] = self._count_rows_any(
            ["msg_scheduled", "scheduled_messages"], "status = 'pending'"
        )
        feature_stats["forwarded_messages"] = self._count_rows_any(
            ["msg_forwarded", "forwarded_messages"]
        )
        feature_stats["voice_messages"] = self._maybe_count_rows(
            "SELECT COUNT(*) as count FROM msg_messages WHERE message_type = 'voice'"
        )
        feature_stats["profiles_with_status"] = self._count_rows(
            "user_profiles", "custom_status_text IS NOT NULL"
        )
        feature_stats["push_tokens"] = self._count_rows("push_tokens")
        feature_stats["webhook_retries_pending"] = self._count_rows(
            "webhook_retry_queue", "status = 'pending'"
        )
        if self._db.table_exists("message_reports"):
            rows = self._db.fetch_all(
                "SELECT category, COUNT(*) as count FROM message_reports GROUP BY category ORDER BY count DESC LIMIT 10"
            )
            feature_stats["report_categories"] = [
                {"category": row["category"], "count": row["count"]} for row in rows
            ]
        else:
            feature_stats["report_categories"] = []
        feature_stats["dm_spam_filters_active"] = self._count_rows(
            "dm_spam_filters", "enabled = 1"
        )
        feature_stats["threads_with_slowmode"] = self._count_rows(
            "thread_threads", "slowmode_interval_ms > 0"
        )
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
        if limit > 100:
            limit = 100

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
                "bot_id": row["bot_id"],
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
        if limit > 100:
            limit = 100

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
