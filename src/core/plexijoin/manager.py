"""
PlexiJoin federation manager for inter-instance connections.

Handles outbound joins, inbound requests, traffic tracking, and health checks.
"""

import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from src.core.admin.logging import AdminLogEntry

logger = logging.getLogger(__name__)

# Inbound route paths on the REMOTE instance that this instance POSTs to.
# Mirrors the routes registered in ``src/api/routes/federation.py``.
FEDERATION_JOIN_REQUEST_PATH = "/api/v1/federation/join-request"
FEDERATION_MESSAGE_PATH = "/api/v1/federation/message"


class RemoteURLBlockedError(ValueError):
    """A remote federation URL failed SSRF validation (scheme/private IP)."""


def _sign_payload(shared_key: str, body: bytes) -> str:
    """HMAC-SHA256 signature over the raw request body using the shared key."""
    import hashlib
    import hmac as _hmac

    return _hmac.new(shared_key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def validate_remote_url(remote_url: str) -> None:
    """SSRF-validate a caller-supplied remote instance URL.

    Requires an https scheme and rejects URLs whose hostname resolves to a
    private/reserved IP using the shared URLValidator from utils.security.

    Raises:
        RemoteURLBlockedError: when the URL is not acceptable.
    """
    if not remote_url or not isinstance(remote_url, str):
        raise RemoteURLBlockedError("remote_url is required")
    parsed_scheme = remote_url.split("://", 1)[0].lower() if "://" in remote_url else ""
    if parsed_scheme != "https":
        raise RemoteURLBlockedError("remote_url must use the https scheme")
    try:
        from src.utils.security import URLValidator

        URLValidator().validate_url_for_request(remote_url)
    except ImportError:  # pragma: no cover - security utils always present
        raise RemoteURLBlockedError("URL validator unavailable")
    except ValueError as e:
        raise RemoteURLBlockedError(f"remote_url blocked: {e}")


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
        # CORRECTNESS FIX: decrypt shared_key for every returned
        # connection so dashboards / federation handlers always see
        # the plaintext key (matches ``get_connection``).
        materialized = [self._materialize_connection(c) for c in connections]

        return {
            "connections": materialized,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }

    def get_connection(self, connection_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific connection by ID.

        CORRECTNESS FIX: the previous implementation returned the
        raw row with ``shared_key_encrypted`` still encrypted, so
        every caller that needed the actual shared key had to call
        ``_decrypt_key`` themselves — and most didn't, leaving
        outbound federation silently broken (the encrypted blob was
        forwarded to the remote instance which could not decrypt it
        because the key was never written into a shared message).
        We now materialise the decrypted ``shared_key`` here so
        callers always see the plaintext they expect.
        """
        row = self.db.fetch_one(
            "SELECT * FROM plexijoin_connections WHERE id = ?",
            (connection_id,),
        )
        if row is None:
            return None
        return self._materialize_connection(row)

    def _materialize_connection(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt the shared_key in-place for a connection row."""
        if row.get("shared_key_encrypted"):
            try:
                row["shared_key"] = self._decrypt_key(row["shared_key_encrypted"])
            except Exception as e:  # pragma: no cover -- keyring failures
                logger.error(
                    "plexijoin: failed to decrypt shared_key for connection %s: %s",
                    row.get("id"),
                    e,
                )
                # Surface as empty string so downstream code's None check
                # is unambiguous without hiding the failure.
                row["shared_key"] = None
        else:
            row["shared_key"] = None
        return row

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

        row_id = (
            self.db.table("plexijoin_connections")
            .insert(
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
            .execute()
        )

        connection = self.db.fetch_one(
            "SELECT * FROM plexijoin_connections WHERE id = ?",
            (row_id,),
        )

        if self.admin_logger:
            self.admin_logger.log_action(
                self.db,
                AdminLogEntry(
                    admin_id=admin_id,
                    action="plexijoin.create",
                    target_type="connection",
                    target_id=row_id,
                    details=f"Created connection to {remote_instance_id}",
                    ip_address=ip_address,
                    user_agent=user_agent,
                ),
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
            self.admin_logger.log_action(
                self.db,
                AdminLogEntry(
                    admin_id=admin_id,
                    action="plexijoin.delete",
                    target_type="connection",
                    target_id=connection_id,
                    details=f"Deleted connection to {connection['remote_instance_id']}",
                    ip_address=ip_address,
                    user_agent=user_agent,
                ),
            )

        return True

    async def test_connection(self, connection_id: int) -> Dict[str, Any]:
        """Test connectivity to a remote instance.

        A successful probe ACTIVATES the connection (pending -> active,
        broken -> active); a failure marks an active connection broken.
        The stored remote_url is SSRF-validated (https + public IP) before
        any outbound request is made.
        """
        import httpx

        connection = self.get_connection(connection_id)
        if not connection:
            return {"success": False, "error": "Connection not found"}

        try:
            validate_remote_url(connection["remote_url"])
        except RemoteURLBlockedError as e:
            return {"success": False, "error": str(e)}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{connection['remote_url']}/api/v1/health")
                is_reachable = response.status_code == 200

                if is_reachable and connection["status"] in ("pending", "broken"):
                    self.activate_connection(connection_id)

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

    def activate_connection(self, connection_id: int) -> bool:
        """Transition a connection to ``active`` (idempotent).

        Called after a successful health probe or when an inbound join
        request from the remote instance is approved, so fresh/pending
        connections no longer dead-end in ``pending``.
        """
        now_ms = int(time.time() * 1000)
        cursor = self.db.execute(
            """
            UPDATE plexijoin_connections
            SET status = 'active',
                connected_at = COALESCE(connected_at, ?),
                last_activity = ?
            WHERE id = ? AND status != 'active'
            """,
            (now_ms, now_ms, connection_id),
        )
        return getattr(cursor, "rowcount", 0) > 0

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

    def submit_inbound_request(
        self,
        remote_instance_id: str,
        remote_url: str,
        requested_by: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an inbound federation join request from a remote instance.

        Called by the HMAC-authenticated inbound federation endpoint after
        the remote's signature has been verified against a known shared
        key. This is the ONLY production path that inserts into
        ``plexijoin_inbound_requests``.
        """
        now_ms = int(time.time() * 1000)
        row_id = (
            self.db.table("plexijoin_inbound_requests")
            .insert(
                {
                    "remote_instance_id": remote_instance_id,
                    "remote_url": remote_url,
                    "requested_by": requested_by,
                    "note": note,
                    "status": "pending",
                    "requested_at": now_ms,
                }
            )
            .execute()
        )
        row = self.db.fetch_one(
            "SELECT * FROM plexijoin_inbound_requests WHERE id = ?", (row_id,)
        )
        logger.info(
            "plexijoin: recorded inbound join request %s from %s",
            row_id,
            remote_instance_id,
        )
        return row

    def approve_request(
        self,
        request_id: int,
        admin_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve an inbound federation request.

        The state transition is an ATOMIC conditional UPDATE
        (``WHERE id = ? AND status = 'pending'``); the rowcount decides
        success so two concurrent admins can never both win (previously
        the pending check was a separate SELECT followed by an
        unconditional UPDATE).
        """
        request = self._transition_request(
            request_id, "approved", admin_id, ip_address, user_agent
        )

        # Lifecycle: approving an inbound request activates any local
        # outbound connection to the same remote instance that is still
        # sitting in ``pending`` (the shared key was already exchanged —
        # the request was signed with it).
        try:
            conn = self.db.fetch_one(
                "SELECT id FROM plexijoin_connections WHERE remote_instance_id = ?",
                (request["remote_instance_id"],),
            )
            if conn:
                self.activate_connection(conn["id"])
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("plexijoin: post-approve activation failed: %s", e)

        return request

    def deny_request(
        self,
        request_id: int,
        admin_id: int,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Deny an inbound federation request (atomic conditional update)."""
        return self._transition_request(
            request_id, "denied", admin_id, ip_address, user_agent
        )

    def _transition_request(
        self,
        request_id: int,
        new_status: str,
        admin_id: int,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> Dict[str, Any]:
        """Atomically move a pending inbound request to ``new_status``."""
        now_ms = int(time.time() * 1000)

        cursor = self.db.execute(
            """
            UPDATE plexijoin_inbound_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?
            WHERE id = ? AND status = 'pending'
            """,  # nosec: B608 - fully parameterized
            (new_status, now_ms, admin_id, request_id),
        )
        if getattr(cursor, "rowcount", 0) <= 0:
            # Distinguish "missing" from "already processed" for callers.
            existing = self.db.fetch_one(
                "SELECT status FROM plexijoin_inbound_requests WHERE id = ?",
                (request_id,),
            )
            if not existing:
                raise ValueError("Request not found")
            raise ValueError("Request already processed")

        request = self.db.fetch_one(
            "SELECT * FROM plexijoin_inbound_requests WHERE id = ?", (request_id,)
        )

        action = (
            "plexijoin.approve_request"
            if new_status == "approved"
            else "plexijoin.deny_request"
        )
        verb = "Approved" if new_status == "approved" else "Denied"

        if self.admin_logger:
            self.admin_logger.log_action(
                self.db,
                AdminLogEntry(
                    admin_id=admin_id,
                    action=action,
                    target_type="inbound_request",
                    target_id=request_id,
                    details=f"{verb} request from {request['remote_instance_id']}",
                    ip_address=ip_address,
                    user_agent=user_agent,
                ),
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

    # === Outbound federation transport ===

    @staticmethod
    def generate_shared_key() -> str:
        """Generate a cryptographically secure shared key for a connection."""
        return secrets.token_urlsafe(32)

    async def _post_signed(
        self,
        remote_url: str,
        shared_key: str,
        path: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """POST an HMAC-signed payload to the remote instance.

        Returns ``{"success": True, "status_code": ...}`` on a 2xx response,
        ``{"success": False, "error": ...}`` otherwise.
        """
        import httpx

        validate_remote_url(remote_url)

        body = json.dumps(payload).encode("utf-8")
        headers = {"content-type": "application/json"}
        if shared_key:
            headers["x-plexijoin-signature"] = _sign_payload(shared_key, body)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    remote_url.rstrip("/") + path, content=body, headers=headers
                )
            ok = 200 <= response.status_code < 300
            return {
                "success": ok,
                "status_code": response.status_code,
                **({} if ok else {"error": f"remote returned {response.status_code}"}),
            }
        except RemoteURLBlockedError:
            raise
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_join_request(self, connection_id: int) -> Dict[str, Any]:
        """Send this instance's join request to the remote endpoint.

        The request is signed with the (pending) connection's shared key —
        the remote verifies the signature against the same key before
        recording the inbound request.
        """
        connection = self.get_connection(connection_id)
        if not connection:
            return {"success": False, "error": "Connection not found"}

        try:
            from src.utils.common_utils.utils.licensing import get_instance_id

            local_instance_id = get_instance_id() or ""
        except Exception:  # pragma: no cover - licensing internals
            local_instance_id = ""

        payload = {
            "instance_id": local_instance_id or f"local-{connection['id']}",
            "remote_url": connection["remote_url"],
            "requested_by": str(connection.get("created_by") or ""),
            "note": connection.get("note"),
        }

        result = await self._post_signed(
            connection["remote_url"],
            connection.get("shared_key") or "",
            FEDERATION_JOIN_REQUEST_PATH,
            payload,
        )
        if not result.get("success"):
            logger.warning(
                "plexijoin: join request delivery to %s failed: %s",
                connection["remote_url"],
                result.get("error"),
            )
        return result

    async def send_message(
        self,
        connection_id: int,
        message_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send a federated message over an ACTIVE connection and record traffic."""
        connection = self.get_connection(connection_id)
        if not connection:
            return {"success": False, "error": "Connection not found"}
        if connection["status"] != "active":
            return {
                "success": False,
                "error": f"Connection is {connection['status']}, not active",
            }

        envelope = {"message_type": message_type, "payload": payload}
        result = await self._post_signed(
            connection["remote_url"],
            connection.get("shared_key") or "",
            FEDERATION_MESSAGE_PATH,
            envelope,
        )
        # Feed the traffic counters on success so messages_today /
        # get_traffic_data reflect real federated messaging.
        if result.get("success"):
            self.record_traffic(connection_id, "outbound", 1)
        else:
            logger.warning(
                "plexijoin: federated message to connection %s failed: %s",
                connection_id,
                result.get("error"),
            )
        return result
