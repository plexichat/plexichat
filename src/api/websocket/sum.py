"""
PlexiSum real-time fabric - Subscriber registry and relay helpers.

This module is the real-time foundation for collaborative workbooks. It
mirrors the artifact fabric (``src.api.websocket.artifacts``):

- keeps an in-memory registry of which users/connections are subscribed to
  which workbook (multi-tab safe, connection tracked), and
- provides async helpers to relay a ``SUM_OP`` delta to the workbook's other
  subscribers and to send a ``SUM_SYNC`` snapshot (current revision + op
  replay) to a single connection for late-joiner bootstrap and conflict
  recovery.

The REST API remains the authoritative persistence path; this fabric only
relays deltas and snapshots. Authorization is revalidated against the Sum
domain before every relay so grant/scope changes take effect immediately.
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

import utils.logger as logger

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.connection import Connection

if TYPE_CHECKING:
    from src.api.websocket.dispatcher import GatewayDispatcher


class SumSubscriptionRegistry:
    """In-memory registry mapping workbook_id -> set of subscribed user_ids."""

    def __init__(self) -> None:
        self._subs: Dict[int, Set[int]] = {}
        # Track subscriptions by connection as well as user. A user may have
        # multiple tabs, and disconnecting one tab must not revive or remove
        # subscriptions belonging to another tab.
        self._connection_subs: Dict[str, Set[int]] = {}
        self._connection_users: Dict[str, int] = {}
        self._lock = threading.Lock()

    def subscribe(
        self, user_id: int, workbook_id: int, connection_id: Optional[str] = None
    ) -> None:
        """Subscribe a user/connection to a workbook."""
        with self._lock:
            self._subs.setdefault(int(workbook_id), set()).add(int(user_id))
            if connection_id:
                self._connection_subs.setdefault(connection_id, set()).add(
                    int(workbook_id)
                )
                self._connection_users[connection_id] = int(user_id)

    def unsubscribe(
        self, user_id: int, workbook_id: int, connection_id: Optional[str] = None
    ) -> None:
        """Unsubscribe a user/connection from a workbook.

        Multi-tab safe: when ``connection_id`` is given, only that connection's
        subscription is removed. The user is dropped from the global
        ``_subs`` set only when no remaining connection by the same user is
        still subscribed to the workbook, so one tab unsubscribing (or being
        torn down) cannot silence SUM_OP relays to the user's other tabs.
        """
        with self._lock:
            wid = int(workbook_id)
            uid = int(user_id)
            if connection_id:
                workbooks = self._connection_subs.get(connection_id)
                if workbooks is not None:
                    workbooks.discard(wid)
                    if not workbooks:
                        self._connection_subs.pop(connection_id, None)
                        self._connection_users.pop(connection_id, None)
            still_subscribed = bool(connection_id) and any(
                wid in conn_workbooks and self._connection_users.get(conn) == uid
                for conn, conn_workbooks in self._connection_subs.items()
            )
            if not still_subscribed:
                subs = self._subs.get(wid)
                if subs is not None:
                    subs.discard(uid)
                    if not subs:
                        self._subs.pop(wid, None)

    def get_subscribers(self, workbook_id: int) -> Set[int]:
        """Return the set of subscribed user_ids for a workbook (copy)."""
        with self._lock:
            return set(self._subs.get(int(workbook_id), set()))

    def unsubscribe_all(self, user_id: int) -> None:
        """Remove a user from every workbook subscription."""
        with self._lock:
            for workbook_id in list(self._subs):
                subs = self._subs[workbook_id]
                subs.discard(int(user_id))
                if not subs:
                    self._subs.pop(workbook_id, None)
            for connection_id, workbooks in list(self._connection_subs.items()):
                if self._connection_users.get(connection_id) == int(user_id):
                    self._connection_subs.pop(connection_id, None)
                    self._connection_users.pop(connection_id, None)

    def unsubscribe_connection(
        self, connection_id: str, user_id: Optional[int] = None
    ) -> None:
        """Remove exactly one connection's subscriptions on WebSocket close."""
        with self._lock:
            workbook_ids = self._connection_subs.pop(connection_id, set())
            tracked_user_id = self._connection_users.pop(connection_id, None)
            effective_user_id = int(user_id) if user_id is not None else tracked_user_id
            for workbook_id in workbook_ids:
                subs = self._subs.get(workbook_id)
                if subs is None:
                    continue
                if effective_user_id is not None:
                    still_subscribed = any(
                        workbook_id in other_workbooks
                        and self._connection_users.get(other_connection)
                        == int(effective_user_id)
                        for other_connection, other_workbooks in self._connection_subs.items()
                    )
                    if not still_subscribed:
                        subs.discard(int(effective_user_id))
                if not subs:
                    self._subs.pop(workbook_id, None)


_sum_subscription_registry = SumSubscriptionRegistry()


def get_sum_subscription_registry() -> SumSubscriptionRegistry:
    """Return the process-wide Sum subscription registry."""
    return _sum_subscription_registry


def _get_sum_manager():
    """Resolve the process-local Sum manager, with a fixture-safe fallback."""
    from src.core.suite import sum as sum_domain

    try:
        return sum_domain.get_manager()
    except RuntimeError:
        import src.api as api
        from src.core.suite.sum.manager import SumManager

        db = api.get_db()
        if db is None:
            raise RuntimeError("PlexiSum database unavailable")
        return SumManager(db, servers_module=api.get_servers())


def _capability_allows_sum() -> bool:
    """Fail closed unless the plexisum capability is fully available."""
    try:
        from src.core.artifacts.capabilities import get_capability, is_usable

        return is_usable(get_capability("plexisum").state)
    except Exception:  # pragma: no cover - defensive
        return False


async def relay_sum_op(
    dispatcher: "GatewayDispatcher",
    workbook_id: int,
    op: Dict[str, object],
    actor_id: int,
    exclude_user_id: Optional[int] = None,
) -> int:
    """
    Fan out a SUM_OP delta to the workbook's other subscribers.

    Subscriptions are only a routing hint: every recipient is revalidated
    against the Sum domain (``SumManager.get_workbook``) before the frame is
    sent, so grant revocations and scope changes take effect without waiting
    for a reconnect. Delivery reuses the dispatcher send path and the
    per-connection rate limit.

    Returns the number of connections the op was delivered to.
    """
    if not _capability_allows_sum():
        return 0
    try:
        manager = _get_sum_manager()
    except RuntimeError:
        return 0

    subscribers = get_sum_subscription_registry().get_subscribers(workbook_id)
    if exclude_user_id is not None:
        subscribers.discard(int(exclude_user_id))
    if not subscribers:
        return 0

    session_manager = dispatcher._session_manager
    connections = session_manager.get_connections_for_users(list(subscribers))

    payload = {
        "op": int(GatewayOpcode.SUM_OP),
        "d": {
            "workbook_id": int(workbook_id),
            "actor_id": int(actor_id),
            "op": op,
        },
    }

    sent = 0
    # Cache authorization results for this relay burst
    _auth_cache: Dict[int, bool] = {}
    for conn in connections:
        if not conn.user_id:
            continue
        uid = int(conn.user_id)
        if uid not in _auth_cache:
            try:
                manager.get_workbook(uid, int(workbook_id))
                _auth_cache[uid] = True
            except Exception:
                _auth_cache[uid] = False
        if not _auth_cache[uid]:
            continue
        if not (
            hasattr(conn, "is_selftest") and conn.is_selftest
        ) and not conn.check_rate_limit(dispatcher._rate_limit_per_minute):
            continue
        try:
            result = await asyncio.wait_for(
                dispatcher._send_to_connection(conn, payload),
                timeout=5,
            )
            if result is True:
                sent += 1
        except Exception:
            pass

    logger.debug(
        f"Relayed SUM_OP for workbook {workbook_id} from {actor_id} "
        f"to {sent}/{len(connections)} connections"
    )
    return sent


async def send_sum_sync(
    connection: Connection,
    workbook_id: int,
    revision: int,
    ops: Optional[List[Dict[str, Any]]] = None,
    rate_limit_per_minute: Optional[int] = None,
) -> bool:
    """
    Send a SUM_SYNC snapshot to a single connection.

    Used to bootstrap late joiners (subscribe) and to recover senders after a
    revision conflict. ``ops`` is the ordered list of persisted operations
    (in ``result_revision`` order) for replay after the client's last known
    revision. ``rate_limit_per_minute`` is the gateway's configured per-connection
    limit; when omitted the connection's own default is used.
    """
    limit = (
        int(rate_limit_per_minute)
        if rate_limit_per_minute is not None
        else getattr(connection, "rate_limit_per_minute", 120)
    )
    if not (
        hasattr(connection, "is_selftest") and connection.is_selftest
    ) and not connection.check_rate_limit(limit):
        logger.debug(f"Sum sync rate limited for connection {connection.connection_id}")
        return False

    payload = {
        "op": int(GatewayOpcode.SUM_SYNC),
        "d": {
            "workbook_id": int(workbook_id),
            "revision": int(revision),
            "ops": ops or [],
        },
    }
    try:
        return await connection.send_json(payload)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Failed to send SUM_SYNC to {connection.connection_id}: {e}")
        return False
