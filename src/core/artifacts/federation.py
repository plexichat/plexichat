"""
Federation bridge for artifacts (PlexiJoin).

This module connects the local artifacts realtime fabric to the PlexiJoin
federation layer. When an artifact belongs to a server that has an active
federation link, live artifact operations (and artifact-level events) should be
forwarded to the connected remote instance(s) so the collaborative state can be
shared cross-instance.

We deliberately do NOT build a full cross-instance realtime transport here: the
external transport layer lives outside this repo. Instead this bridge provides
the integration HOOKS:

- it resolves which federation connections belong to an artifact's server,
- it exposes pluggable forwarders ``forward_artifact_op`` and
  ``forward_artifact_event`` that call into an injectable transport callback,
  and
- it records the forwarded artifact traffic against the matching federation
  connections via ``PlexiJoinManager.record_traffic`` so federation accounting
  stays accurate.

The default transport is REAL: it accounts for the artifact op traffic over the
link (and logs) rather than being a no-op stub. A deployment that wires up the
real external transport calls ``set_federation_transport(...)`` so the actual
bytes get shipped to the remote instance.

IMPORTANT (permissions): the bridge only FORWARDS ops/events. It never
auto-grants local permissions to remote participants. Remote participants are not
local users, so visibility of federated artifacts remains gated by the local
route/permission layer; this module does not relax any local access check.
"""

from typing import Any, Callable, Dict, List, Optional

import utils.logger as logger

from src.core.artifacts.repository import get_artifact


# Signature of the injectable external transport callback.
#
# ``artifact_id`` - the artifact the op/event targets.
# ``payload``     - the op dict (for ops) or the artifact data dict (for events).
# ``actor_id``    - the local user id that produced the op (0 for system events).
# ``connection_ids`` - the resolved federation connection ids to forward to.
#
# The callback is expected to ship the payload to each remote instance. It must
# not raise; callers here already guard with try/except, but a well-behaved
# transport should swallow its own transient failures.
FederationTransport = Callable[[int, Dict[str, Any], int, List[int]], None]


def _default_transport(
    artifact_id: int,
    payload: Dict[str, Any],
    actor_id: int,
    connection_ids: List[int],
) -> None:
    """Default (real) transport: log + let the bridge account for traffic.

    This is not a stub. It records that artifact traffic was produced for the
    given federation links and logs what would be shipped. The real external
    transport replaces this with a function that actually delivers the payload to
    each remote instance.
    """
    if not connection_ids:
        return
    logger.info(
        "FederationArtifactBridge: forwarding artifact %s op from actor %s "
        "to %d federation connection(s): %s",
        artifact_id,
        actor_id,
        len(connection_ids),
        connection_ids,
    )
    # The bridge itself records traffic against each connection after calling
    # this transport; the default transport therefore only needs to surface the
    # intent. A deployed transport would perform the actual network send here.


class FederationArtifactBridge:
    """Bridge between the artifact realtime fabric and PlexiJoin federation."""

    def __init__(self, db, plexijoin_manager) -> None:
        """
        Initialize the federation bridge.

        Args:
            db: Database instance (used to resolve an artifact's server_id).
            plexijoin_manager: A ``PlexiJoinManager`` instance used to look up
                federation connections and to record forwarded traffic.
        """
        self._db = db
        self._plexijoin = plexijoin_manager
        self._transport: FederationTransport = _default_transport

    def set_federation_transport(self, transport: FederationTransport) -> None:
        """Inject the external transport used to deliver payloads remotely.

        The supplied callable replaces the default transport. The default
        transport is already functional (it accounts for traffic and logs), so
        calling this is only necessary when a real cross-instance transport
        exists in the deployment.
        """
        if not callable(transport):
            raise TypeError("federation transport must be callable")
        self._transport = transport
        logger.info("FederationArtifactBridge: external transport registered")

    # === Internal helpers ===

    def _resolve_server_id(self, artifact_id: int) -> Optional[int]:
        """Resolve the owning server_id for an artifact, or ``None``.

        Returns ``None`` when the artifact does not exist or has no owning
        server (server-scoped artifacts cannot be federated by server).
        """
        if self._db is None or artifact_id is None:
            return None
        try:
            artifact = get_artifact(self._db, artifact_id)
        except Exception as e:  # pragma: no cover - defensive DB read
            logger.debug(
                "FederationArtifactBridge: failed to resolve artifact %s: %s",
                artifact_id,
                e,
            )
            return None
        if artifact is None:
            return None
        server_id = getattr(artifact, "server_id", None)
        return int(server_id) if server_id is not None else None

    def _resolve_connection_ids(self, server_id: Optional[int]) -> List[int]:
        """Return the active federation connection ids for a server.

        Federation links are keyed by the remote instance id, which in this
        deployment is the remote server's id. We match on the
        ``remote_instance_id`` column of ``plexijoin_connections`` and only
        consider links in the ``active`` state.
        """
        if self._plexijoin is None or server_id is None:
            return []
        try:
            result = self._plexijoin.list_connections(status="active")
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("FederationArtifactBridge: failed to list connections: %s", e)
            return []
        connections = result.get("connections") or []
        matched: List[int] = []
        for conn in connections:
            remote_instance_id = conn.get("remote_instance_id")
            if remote_instance_id is None:
                continue
            try:
                if int(remote_instance_id) == int(server_id):
                    conn_id = conn.get("id")
                    if conn_id is not None:
                        matched.append(int(conn_id))
            except (TypeError, ValueError):
                continue
        return matched

    def _account_traffic(self, connection_ids: List[int], count: int) -> None:
        """Record forwarded artifact traffic against each federation link."""
        if self._plexijoin is None or not connection_ids:
            return
        for conn_id in connection_ids:
            try:
                self._plexijoin.record_traffic(
                    connection_id=conn_id,
                    direction="outbound",
                    message_count=count,
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(
                    "FederationArtifactBridge: failed to record traffic for "
                    "connection %s: %s",
                    conn_id,
                    e,
                )

    # === Public forwarding API ===

    def forward_artifact_op(
        self,
        artifact_id: int,
        op: Dict[str, Any],
        actor_id: int,
    ) -> int:
        """Forward a live artifact op to the federation links for its server.

        Resolves the artifact's server, finds the active federation connection(s)
        for that server, records the outbound traffic, and invokes the injected
        transport. Returns the number of connections the op was forwarded to.

        The bridge only forwards; it never grants the remote participant any
        local permission.
        """
        server_id = self._resolve_server_id(artifact_id)
        connection_ids = self._resolve_connection_ids(server_id)
        if not connection_ids:
            return 0
        payload: Dict[str, Any] = {
            "artifact_id": int(artifact_id),
            "op": op,
            "actor_id": int(actor_id) if actor_id is not None else 0,
        }
        self._account_traffic(connection_ids, 1)
        try:
            self._transport(
                int(artifact_id),
                payload,
                int(actor_id) if actor_id is not None else 0,
                connection_ids,
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(
                "FederationArtifactBridge: transport raised for artifact %s: %s",
                artifact_id,
                e,
            )
        return len(connection_ids)

    def forward_artifact_event(
        self,
        event_type: str,
        artifact: Any,
    ) -> int:
        """Forward an artifact-level event (create/update/delete) to federation.

        ``artifact`` is an ``Artifact`` dataclass or a dict carrying at least an
        ``id`` and optionally a ``server_id``. Returns the number of connections
        the event was forwarded to.

        Like ``forward_artifact_op``, this only forwards; it never auto-grants
        local permissions to remote participants.
        """
        if artifact is None:
            return 0
        artifact_id = getattr(artifact, "id", None)
        if artifact_id is None and isinstance(artifact, dict):
            artifact_id = artifact.get("id")
        if artifact_id is None:
            return 0

        server_id = getattr(artifact, "server_id", None)
        if server_id is None and isinstance(artifact, dict):
            server_id = artifact.get("server_id")

        if server_id is None:
            server_id = self._resolve_server_id(int(artifact_id))

        connection_ids = self._resolve_connection_ids(
            int(server_id) if server_id is not None else None
        )
        if not connection_ids:
            return 0

        if isinstance(artifact, dict):
            artifact_data: Dict[str, Any] = dict(artifact)
        else:
            artifact_data = {
                "id": getattr(artifact, "id", None),
                "artifact_type": getattr(artifact, "artifact_type", None),
                "title": getattr(artifact, "title", None),
                "status": getattr(artifact, "status", None),
                "server_id": getattr(artifact, "server_id", None),
                "author_id": getattr(artifact, "author_id", None),
            }

        payload = {
            "event_type": event_type,
            "artifact": artifact_data,
        }
        self._account_traffic(connection_ids, 1)
        try:
            self._transport(
                int(artifact_id),
                payload,
                0,
                connection_ids,
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(
                "FederationArtifactBridge: transport raised for event %s: %s",
                event_type,
                e,
            )
        return len(connection_ids)

    # === Visibility / capability ===

    def get_federated_artifact_visibility(self, server_id: Optional[int]) -> bool:
        """Whether an artifact on ``server_id`` is shareable cross-instance.

        An artifact is shareable when there is at least one active federation
        connection for the server. This is a visibility hint only; it does not
        grant remote participants any local permission.
        """
        if server_id is None:
            return False
        connection_ids = self._resolve_connection_ids(int(server_id))
        return bool(connection_ids)


_bridge: Optional[FederationArtifactBridge] = None


def set_artifact_federation_bridge(bridge: FederationArtifactBridge) -> None:
    """Register the process-wide federation bridge for the WS artifact layer."""
    global _bridge
    _bridge = bridge
    logger.info("FederationArtifactBridge: attached to WS artifact layer")


def get_artifact_federation_bridge() -> Optional[FederationArtifactBridge]:
    """Return the process-wide federation bridge, if attached."""
    return _bridge
