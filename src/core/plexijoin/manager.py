"""
PlexiJoin federation manager for inter-instance connections.

Handles outbound joins, inbound requests, traffic tracking, and health checks.
"""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PlexiJoinManager:
    """Manages PlexiJoin federation operations."""

    def __init__(self, db, admin_logger=None, encryption_service=None):
        """
        Initialize PlexiJoin manager.

        Args:
            db: Database instance
            admin_logger: Admin audit logger
            encryption_service: Encryption service for shared keys
        """
        self.db = db
        self.admin_logger = admin_logger
        self.encryption_service = encryption_service

    def _encrypt_key(self, shared_key: str) -> str:
        """Encrypt shared key using keyring infrastructure."""
        if self.encryption_service:
            return self.encryption_service.encrypt(shared_key)
        raise RuntimeError("Encryption service not available")

    def _decrypt_key(self, encrypted_key: str) -> str:
        """Decrypt shared key using keyring infrastructure."""
        if self.encryption_service:
            return self.encryption_service.decrypt(encrypted_key)
        raise RuntimeError("Encryption service not available")

    # === Outbound Connections ===

    def list_connections(
        self,
        status: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> Dict[str, Any]:
        """List all outbound federation connections."""
        query = "SELECT * FROM plexijoin_connections"
        params = []

        if status:
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC"

        count_query = f"SELECT COUNT(*) as total FROM ({query})"
        total = self.db.fetch_one(count_query, params)["total"]

        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])

        connections = self.db.fetch_all(query, params)

        return {
            "connections": connections,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    def get_connection(self, connection_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific connection by ID."""
        return self.db.fetch_one(
            "SELECT * FROM plexijoin_connections WHERE id = ?",
            (connection_id,),
        )

    def create_connection(
        self,
        remote_instance_id: str,
        remote_url: str,
        shared_key: str,
        note: Optional[str],
        admin_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new outbound federation connection."""
        now_ms = int(time.time() * 1000)
        encrypted_key = self._encrypt_key(shared_key)

        result = self.db.table("plexijoin_connections").insert(
            {
                "remote_instance_id": remote_instance_id,
                "remote_url": remote_url,
                "shared_key_encrypted": encrypted_key,
                "status": "pending",
                "note": note,
                "created_at": now_ms,
                "created_by": admin_id,
            }
        )

        connection = self.db.fetch_one(
            "SELECT * FROM plexijoin_connections WHERE id = ?",
            (result.lastrowid,),
        )

        if self.admin_logger:
            self.admin_logger.log(
                admin_id=admin_id,
                action="plexijoin.create",
                target_type="connection",
                target_id=result.lastrowid,
                details=f"Created connection to {remote_instance_id}",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return connection

    def delete_connection(
        self,
        connection_id: int,
        admin_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> bool:
        """Disconnect and delete an outbound connection."""
        connection = self.get_connection(connection_id)
        if not connection:
            return False

        self.db.execute(
            "DELETE FROM plexijoin_connections WHERE id = ?",
            (connection_id,),
        )

        if self.admin_logger:
            self.admin_logger.log(
                admin_id=admin_id,
                action="plexijoin.delete",
                target_type="connection",
                target_id=connection_id,
                details=f"Deleted connection to {connection['remote_instance_id']}",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return True

    async def test_connection(self, connection_id: int) -> Dict[str, Any]:
        """Test connectivity to a remote instance."""
        import httpx

        connection = self.get_connection(connection_id)
        if not connection:
            return {"success": False, "error": "Connection not found"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{connection['remote_url']}/api/v1/health")
                is_reachable = response.status_code == 200

                if is_reachable and connection["status"] == "broken":
                    self.db.execute(
                        "UPDATE plexijoin_connections SET status = 'active', last_activity = ? WHERE id = ?",
                        (int(time.time() * 1000), connection_id),
                    )

                return {
                    "success": is_reachable,
                    "status_code": response.status_code,
                    "remote_status": response.json() if is_reachable else None,
                }
        except Exception as e:
            if connection["status"] == "active":
                self.db.execute(
                    "UPDATE plexijoin_connections SET status = 'broken' WHERE id = ?",
                    (connection_id,),
                )

            return {"success": False, "error": str(e)}

    # === Inbound Requests ===

    def list_requests(
        self,
        status: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> Dict[str, Any]:
        """List inbound federation requests."""
        query = "SELECT * FROM plexijoin_inbound_requests"
        params = []

        if status:
            query += " WHERE status = ?"
            params.append(status)

        query += " ORDER BY requested_at DESC"

        count_query = f"SELECT COUNT(*) as total FROM ({query})"
        total = self.db.fetch_one(count_query, params)["total"]

        query += " LIMIT ? OFFSET ?"
        params.extend([per_page, (page - 1) * per_page])

        requests = self.db.fetch_all(query, params)

        return {
            "requests": requests,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    def approve_request(
        self,
        request_id: int,
        admin_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve an inbound federation request."""
        now_ms = int(time.time() * 1000)

        request = self.db.fetch_one(
            "SELECT * FROM plexijoin_inbound_requests WHERE id = ?",
            (request_id,),
        )

        if not request:
            raise ValueError("Request not found")

        if request["status"] != "pending":
            raise ValueError("Request already processed")

        self.db.execute(
            "UPDATE plexijoin_inbound_requests SET status = 'approved', reviewed_at = ?, reviewed_by = ? WHERE id = ?",
            (now_ms, admin_id, request_id),
        )

        if self.admin_logger:
            self.admin_logger.log(
                admin_id=admin_id,
                action="plexijoin.approve_request",
                target_type="inbound_request",
                target_id=request_id,
                details=f"Approved request from {request['remote_instance_id']}",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return request

    def deny_request(
        self,
        request_id: int,
        admin_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deny an inbound federation request."""
        now_ms = int(time.time() * 1000)

        request = self.db.fetch_one(
            "SELECT * FROM plexijoin_inbound_requests WHERE id = ?",
            (request_id,),
        )

        if not request:
            raise ValueError("Request not found")

        if request["status"] != "pending":
            raise ValueError("Request already processed")

        self.db.execute(
            "UPDATE plexijoin_inbound_requests SET status = 'denied', reviewed_at = ?, reviewed_by = ? WHERE id = ?",
            (now_ms, admin_id, request_id),
        )

        if self.admin_logger:
            self.admin_logger.log(
                admin_id=admin_id,
                action="plexijoin.deny_request",
                target_type="inbound_request",
                target_id=request_id,
                details=f"Denied request from {request['remote_instance_id']}",
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return request

    # === Status & Analytics ===

    def get_status_summary(self) -> Dict[str, Any]:
        """Get federation health summary."""
        active = self.db.fetch_one(
            "SELECT COUNT(*) as count FROM plexijoin_connections WHERE status = 'active'"
        )["count"]

        broken = self.db.fetch_one(
            "SELECT COUNT(*) as count FROM plexijoin_connections WHERE status = 'broken'"
        )["count"]

        pending_requests = self.db.fetch_one(
            "SELECT COUNT(*) as count FROM plexijoin_inbound_requests WHERE status = 'pending'"
        )["count"]

        today_start = int(time.time() * 1000) - (24 * 60 * 60 * 1000)
        messages_today = self.db.fetch_one(
            "SELECT COALESCE(SUM(message_count), 0) as total FROM plexijoin_traffic_log WHERE recorded_at >= ?",
            (today_start,),
        )["total"]

        return {
            "active_connections": active,
            "broken_connections": broken,
            "pending_requests": pending_requests,
            "messages_today": messages_today,
        }

    def get_traffic_data(
        self,
        hours: int = 24,
    ) -> List[Dict[str, Any]]:
        """Get traffic data for the specified hours."""
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (hours * 60 * 60 * 1000)

        rows = self.db.fetch_all(
            """
            SELECT 
                direction,
                recorded_at,
                SUM(message_count) as message_count
            FROM plexijoin_traffic_log
            WHERE recorded_at >= ?
            GROUP BY direction, recorded_at
            ORDER BY recorded_at ASC
            """,
            (start_ms,),
        )

        return rows

    def record_traffic(
        self,
        connection_id: int,
        direction: str,
        message_count: int,
    ) -> None:
        """Record traffic for a connection."""
        now_ms = int(time.time() * 1000)

        self.db.execute(
            """
            INSERT INTO plexijoin_traffic_log 
            (connection_id, direction, message_count, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (connection_id, direction, message_count, now_ms),
        )

        if direction == "inbound":
            self.db.execute(
                "UPDATE plexijoin_connections SET messages_in = messages_in + ?, last_activity = ? WHERE id = ?",
                (message_count, now_ms, connection_id),
            )
        else:
            self.db.execute(
                "UPDATE plexijoin_connections SET messages_out = messages_out + ?, last_activity = ? WHERE id = ?",
                (message_count, now_ms, connection_id),
            )
