"""
Artifacts real-time fabric - Subscriber registry and relay helpers.

This module is the real-time foundation for collaborative artifacts
(live whiteboards, shared code editors, and similar canvases). It keeps an
in-memory registry of which users are subscribed to which artifact, and
provides async helpers to:

- relay a realtime ``ARTIFACT_OP`` delta to all other subscribers of an
  artifact (the caller persists if needed; this module only relays), and
- emit an ``ARTIFACT_SYNC`` full snapshot to a single connection (used to
  bootstrap late joiners).

The dispatcher import is deferred to call time (via TYPE_CHECKING) to avoid a
circular import between this module and ``dispatcher.py``.
"""

from typing import Dict, Optional, Set, TYPE_CHECKING
import asyncio
import threading

import utils.logger as logger

from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.connection import Connection
from src.api.websocket.session import SessionManager

if TYPE_CHECKING:
    from src.api.websocket.dispatcher import GatewayDispatcher


class ArtifactSubscriptionRegistry:
    """In-memory registry mapping artifact_id -> set of subscribed user_ids."""

    def __init__(self) -> None:
        self._subs: Dict[int, Set[int]] = {}
        # Track subscriptions by connection as well as user. A user may have
        # multiple tabs, and disconnecting one tab must not revive or remove
        # subscriptions belonging to another tab.
        self._connection_subs: Dict[str, Set[int]] = {}
        self._connection_users: Dict[str, int] = {}
        self._lock = threading.Lock()

    def subscribe(
        self, user_id: int, artifact_id: int, connection_id: Optional[str] = None
    ) -> None:
        """Subscribe a user/connection to an artifact."""
        with self._lock:
            self._subs.setdefault(artifact_id, set()).add(user_id)
            if connection_id:
                self._connection_subs.setdefault(connection_id, set()).add(artifact_id)
                self._connection_users[connection_id] = int(user_id)

    def unsubscribe(
        self, user_id: int, artifact_id: int, connection_id: Optional[str] = None
    ) -> None:
        """Unsubscribe a user/connection from an artifact."""
        with self._lock:
            subs = self._subs.get(artifact_id)
            if subs is not None:
                subs.discard(user_id)
                if not subs:
                    self._subs.pop(artifact_id, None)
            if connection_id:
                artifacts = self._connection_subs.get(connection_id)
                if artifacts is not None:
                    artifacts.discard(artifact_id)
                    if not artifacts:
                        self._connection_subs.pop(connection_id, None)
                        self._connection_users.pop(connection_id, None)

    def get_subscribers(self, artifact_id: int) -> Set[int]:
        """Return the set of subscribed user_ids for an artifact (copy)."""
        with self._lock:
            return set(self._subs.get(artifact_id, set()))

    def unsubscribe_all(self, user_id: int) -> None:
        """Remove a user from every artifact subscription."""
        with self._lock:
            for artifact_id in list(self._subs):
                subs = self._subs[artifact_id]
                subs.discard(user_id)
                if not subs:
                    self._subs.pop(artifact_id, None)
            for connection_id, artifacts in list(self._connection_subs.items()):
                if self._connection_users.get(connection_id) == int(user_id):
                    self._connection_subs.pop(connection_id, None)
                    self._connection_users.pop(connection_id, None)

    def unsubscribe_connection(
        self, connection_id: str, user_id: Optional[int] = None
    ) -> None:
        """Remove exactly one connection's subscriptions on WebSocket close."""
        with self._lock:
            artifact_ids = self._connection_subs.pop(connection_id, set())
            tracked_user_id = self._connection_users.pop(connection_id, None)
            effective_user_id = user_id if user_id is not None else tracked_user_id
            for artifact_id in artifact_ids:
                subs = self._subs.get(artifact_id)
                if subs is None:
                    continue
                if effective_user_id is not None:
                    still_subscribed = any(
                        artifact_id in other_artifacts
                        and self._connection_users.get(other_connection)
                        == int(effective_user_id)
                        for other_connection, other_artifacts in self._connection_subs.items()
                    )
                    if not still_subscribed:
                        subs.discard(int(effective_user_id))
                if not subs:
                    self._subs.pop(artifact_id, None)


_artifact_subscription_registry = ArtifactSubscriptionRegistry()


def get_artifact_subscription_registry() -> ArtifactSubscriptionRegistry:
    """Return the process-wide artifact subscription registry."""
    return _artifact_subscription_registry


async def relay_artifact_op(
    dispatcher: "GatewayDispatcher",
    artifact_id: int,
    op: Dict[str, object],
    actor_id: int,
    exclude_user_id: Optional[int] = None,
) -> int:
    """
    Fan out an ARTIFACT_OP delta to the artifact's subscribers.

    Looks up the subscribers via the dispatcher's session manager backed
    artifact subscription registry and sends an ``ARTIFACT_OP`` payload to
    each. The op dict is NOT persisted here; the caller (a later group) is
    responsible for persistence.

    Args:
        dispatcher: Gateway dispatcher used to send frames.
        artifact_id: Artifact the op targets.
        op: The realtime op payload (must include ``op_type``).
        actor_id: User id that produced the op.
        exclude_user_id: Optionally skip one user (typically the actor).

    Returns:
        Number of connections the op was delivered to.
    """
    registry = get_artifact_subscription_registry()
    # Subscriptions are only a routing hint. Revalidate the artifact and the
    # recipient's current capability/scope before every relay so access changes
    # take effect without waiting for a reconnect.
    try:
        import src.api as api
        from src.core.artifacts.repository import get_artifact
        from src.core.artifacts.capabilities import capability_allows_artifact

        db = api.get_db()
        artifact = get_artifact(db, artifact_id) if db is not None else None
        if artifact is None or not capability_allows_artifact(artifact.artifact_type):
            return 0
    except Exception:
        return 0

    subscribers = registry.get_subscribers(artifact_id)
    if exclude_user_id is not None:
        subscribers.discard(exclude_user_id)

    if not subscribers:
        return 0

    session_manager: SessionManager = dispatcher._session_manager
    connections = session_manager.get_connections_for_users(list(subscribers))

    payload = {
        "op": int(GatewayOpcode.ARTIFACT_OP),
        "d": {
            "artifact_id": int(artifact_id),
            "actor_id": int(actor_id),
            "op": op,
        },
    }

    sent = 0
    from src.api.websocket.handlers.artifacts import ArtifactHandler

    access_checker = ArtifactHandler()
    for conn in connections:
        if not access_checker._has_artifact_access(conn, artifact, "artifact.view"):
            continue
        if not (
            hasattr(conn, "is_selftest") and conn.is_selftest
        ) and not conn.check_rate_limit(dispatcher._rate_limit_per_minute):
            logger.debug(
                f"Artifact op rate limited for user {getattr(conn, 'user_id', 'unknown')}, connection {conn.connection_id}"
            )
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
        f"Relayed ARTIFACT_OP for artifact {artifact_id} from {actor_id} "
        f"to {sent}/{len(connections)} connections"
    )

    # Federation hook: after the local fan-out, forward the op to any
    # federation links whose remote server owns this artifact. This must never
    # break local relay, so failures are isolated to the federation layer.
    try:
        from src.core.artifacts.federation import get_artifact_federation_bridge

        bridge = get_artifact_federation_bridge()
        if bridge is not None:
            bridge.forward_artifact_op(
                artifact_id=artifact_id,
                op=op,
                actor_id=actor_id,
            )
    except Exception as e:  # pragma: no cover - federation is best-effort
        logger.debug(
            f"Federation forward of ARTIFACT_OP for artifact {artifact_id} "
            f"failed (non-fatal): {e}"
        )

    return sent


async def send_artifact_sync(
    connection: Connection,
    artifact_id: int,
    snapshot: Dict[str, object],
    ops: Optional[list] = None,
) -> bool:
    """
    Send an ARTIFACT_SYNC full snapshot to a single connection.

    Used to bootstrap a late joiner. The snapshot dict is provided by the
    caller (typically fetched from the artifact repository); this function
    only builds and emits the gateway payload through the connection's own
    send path, respecting its rate limit.

    ``ops`` is an optional list of persisted operations (in ``seq`` order)
    for late-joiner replay. Each entry is ``{seq, op_type, actor_id,
    created_at, op}``.

    Args:
        connection: Target connection.
        artifact_id: Artifact the snapshot belongs to.
        snapshot: Full snapshot payload (caller-provided).
        ops: Optional persisted ops to replay to the joiner.

    Returns:
        True if the frame was sent successfully.
    """
    if not (
        hasattr(connection, "is_selftest") and connection.is_selftest
    ) and not connection.check_rate_limit(120):
        logger.debug(
            f"Artifact sync rate limited for connection {connection.connection_id}"
        )
        return False

    payload = {
        "op": int(GatewayOpcode.ARTIFACT_SYNC),
        "d": {
            "artifact_id": int(artifact_id),
            "snapshot": snapshot,
            "ops": ops or [],
        },
    }
    try:
        return await connection.send_json(payload)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Failed to send ARTIFACT_SYNC to {connection.connection_id}: {e}")
        return False
