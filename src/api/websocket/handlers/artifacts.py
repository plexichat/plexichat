"""
Artifact handlers - Handle artifact real-time fabric opcodes.

Registers handlers for:

- ``ARTIFACT_SUBSCRIBE`` (60): register the connection's user as a subscriber
  of an artifact, enforcing RBAC via ``artifact.view`` permission or
  conversation membership before granting access.
- ``ARTIFACT_UNSUBSCRIBE`` (61): remove the subscription.
- ``ARTIFACT_OP`` (62): validate the op payload shape, persist it to the
  ordered ``artifact_ops`` log, and relay the op to the artifact's other
  subscribers. Transient op types (cursor / presence / snapshot requests) are
  relayed but never persisted.

Delivery reuses the existing dispatcher send path and the per-connection rate
limit (no new rate-limit mechanism is introduced).
"""

import asyncio
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

import src.api as api
import utils.config as config
import utils.logger as logger

from src.api.websocket.opcodes import GatewayCloseCode
from src.api.websocket.connection import Connection
from src.api.websocket.artifacts import (
    cap_snapshot_payload,
    get_artifact_subscription_registry,
    relay_artifact_op,
    send_artifact_ack,
    send_artifact_op_rejected,
    send_artifact_sync,
)
from src.core.artifacts.repository import get_artifact
from src.core.artifacts.capabilities import capability_allows_artifact
from src.core.artifacts.revision import payload_revision

if TYPE_CHECKING:
    from src.api.websocket.dispatcher import GatewayDispatcher


class ArtifactHandler:
    """Handles artifact-related real-time opcodes.

    Manages subscribe/unsubscribe of artifact subscriptions and relays ops
    between subscribers. Snapshot delivery fetches live artifact data from
    the repository.
    """

    def __init__(self) -> None:
        self._registry = get_artifact_subscription_registry()

    def _has_artifact_access(
        self,
        connection: Connection,
        artifact: Any,
        permission: str = "artifact.view",
    ) -> bool:
        """Return whether an authenticated connection may access an artifact.

        ``ARTIFACT_OP`` passes ``artifact.edit`` explicitly; viewing an
        artifact must never implicitly grant mutation rights.
        """
        if not connection.user_id or artifact is None:
            return False
        if not capability_allows_artifact(
            artifact.artifact_type, config.get("artifacts", {}) or {}
        ):
            return False

        server_id = artifact.server_id
        conversation_id = artifact.conversation_id
        if server_id is not None:
            servers_mod = api.get_servers()
            if servers_mod is None:
                return False
            from src.core.servers.exceptions import PermissionDeniedError

            try:
                servers_mod.require_permission(
                    connection.user_id, server_id, permission
                )
            except PermissionDeniedError:
                return False
            return True

        if conversation_id is not None:
            messaging_mod = api.get_messaging()
            if messaging_mod is None:
                return False
            try:
                return bool(
                    messaging_mod.is_participant(conversation_id, connection.user_id)
                )
            except Exception:
                return False

        return connection.user_id == artifact.author_id

    async def handle_artifact_subscribe(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
        dispatcher: Any = None,
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle ARTIFACT_SUBSCRIBE opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        artifact_id = data.get("artifact_id")
        if artifact_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            artifact_id = int(artifact_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid artifact_id type in subscribe: {type(artifact_id)}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        db = api.get_db()
        artifact = get_artifact(db, artifact_id) if db else None

        # RBAC: verify the caller has read access to the artifact's scope.
        # Never register a subscription for an unknown artifact. Otherwise a
        # later artifact with the same id could inherit an unauthorized stale
        # subscription.
        #
        # A missing or forbidden artifact must NOT close the gateway: closing
        # with NOT_AUTHENTICATED (4003) makes clients believe their session
        # died and re-authenticate in a loop (fresh ticket → identify →
        # resubscribe → closed again). Auth already succeeded above; deny the
        # subscription only and keep the connection alive.
        if artifact is None:
            logger.warning(
                f"User {connection.user_id} subscribed to unknown artifact {artifact_id}; ignoring"
            )
            return None, None, None
        if not self._has_artifact_access(connection, artifact):
            logger.debug(
                f"User {connection.user_id} denied access to artifact {artifact_id}; "
                "ignoring subscribe"
            )
            return None, None, None

        self._registry.subscribe(
            connection.user_id, artifact_id, connection.connection_id
        )
        logger.debug(f"User {connection.user_id} subscribed to artifact {artifact_id}")

        # L36: a payload can reach ~1 MB; cap what the gateway ships to a
        # late joiner. Oversized payloads are omitted from the sync frame
        # (flagged) so the client fetches the body over REST instead of the
        # server pushing N x 1 MB frames for N concurrent joins.
        snapshot, snapshot_truncated = cap_snapshot_payload(artifact.payload or {})

        # Replay the artifact's persisted ops so a late joiner can catch up on
        # changes that happened while it was offline. The client may hint at
        # ``base_seq`` (the last op seq it already applied) to skip history.
        ops: list = []
        base_seq = data.get("base_seq") or 0
        try:
            base_seq = int(base_seq)
        except (TypeError, ValueError):
            base_seq = 0
        if api.get_db() is not None:
            try:
                from src.core.artifacts.manager import ArtifactManager

                manager = ArtifactManager(
                    api.get_db(), config.get("artifacts", {}) or {}
                )
                ops = manager.list_ops(artifact_id, after_seq=base_seq, limit=500)
            except Exception as e:
                logger.debug(f"Failed to load artifact ops for replay: {e}")

        rate_limit_per_minute = getattr(dispatcher, "_rate_limit_per_minute", None)
        try:
            sent = await send_artifact_sync(
                connection,
                artifact_id,
                snapshot,
                ops=ops,
                rate_limit_per_minute=rate_limit_per_minute,
                snapshot_truncated=snapshot_truncated,
                rev=getattr(artifact, "rev", 0),
            )
            if not sent:
                # The sync was rejected (rate limit or send failure): do not
                # leave a half-registered subscription behind that would
                # silently miss later deltas.
                self._registry.unsubscribe(
                    connection.user_id, artifact_id, connection.connection_id
                )
        except Exception as e:
            logger.debug(f"Failed to send artifact sync: {e}")
            self._registry.unsubscribe(
                connection.user_id, artifact_id, connection.connection_id
            )

        return None, None, None

    async def handle_artifact_unsubscribe(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle ARTIFACT_UNSUBSCRIBE opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        artifact_id = data.get("artifact_id")
        if artifact_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            artifact_id = int(artifact_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid artifact_id type in unsubscribe: {type(artifact_id)}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        self._registry.unsubscribe(
            connection.user_id, artifact_id, connection.connection_id
        )
        logger.debug(
            f"User {connection.user_id} unsubscribed from artifact {artifact_id}"
        )
        return None, None, None

    async def handle_artifact_op(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
        dispatcher: "GatewayDispatcher",
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle ARTIFACT_OP opcode by relaying to other subscribers."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        artifact_id = data.get("artifact_id")
        op = data.get("op")
        if artifact_id is None or not isinstance(op, dict):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        op_type = op.get("op_type")
        if not isinstance(op_type, str) or not op_type or len(op_type) > 64:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)
        # Preserve (and sanitize) the sender's ``client_id`` so the relay AND
        # the ack echo both carry it back. Clients use it to recognise their
        # own ops; without it every echo re-applies as a remote edit.
        raw_client_id = op.get("client_id")
        if raw_client_id is not None:
            normalized_client_id = str(raw_client_id)[:128]
            if normalized_client_id:
                op["client_id"] = normalized_client_id
        try:
            import json

            if len(json.dumps(op, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
                return None, None, int(GatewayCloseCode.DECODE_ERROR)
        except (TypeError, ValueError):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            artifact_id = int(artifact_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid artifact_id type in op: {type(artifact_id)}")
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        # The artifact must exist and the caller must have edit access. Do not
        # relay operations against placeholder ids. A missing or forbidden
        # artifact must not close the gateway with NOT_AUTHENTICATED — that
        # would trap clients in an auth reconnect loop for a resource-level
        # denial. Drop the op instead; the connection stays alive.
        db = api.get_db()
        artifact = get_artifact(db, artifact_id) if db else None
        if artifact is None or not self._has_artifact_access(
            connection, artifact, "artifact.edit"
        ):
            logger.debug(
                f"User {connection.user_id} op on artifact {artifact_id} denied or "
                "artifact unknown; dropping op"
            )
            return None, None, None

        # Rev guard: an ``edit`` op carrying a numeric ``rev`` that is OLDER
        # than the artifact's committed revision is stale — a concurrent write
        # already landed. Persisting or relaying it would pollute the ops log
        # with superseded full-content edits and let peers re-apply them;
        # reject it instead and tell the sender to re-fetch/re-base. Ops
        # without a numeric rev (strokes, cursors, PlexiScribe deltas, or the
        # equal-rev echo of this tab's own just-committed write) pass through.
        op_rev = payload_revision(op)
        if (
            artifact is not None
            and op_type == "edit"
            and op_rev is not None
            and op_rev < int(getattr(artifact, "rev", 0) or 0)
        ):
            logger.debug(
                f"User {connection.user_id} stale edit op (rev {op_rev} < "
                f"{artifact.rev}) on artifact {artifact_id}; rejecting"
            )
            try:
                await send_artifact_op_rejected(
                    connection,
                    artifact_id,
                    op,
                    current_rev=int(getattr(artifact, "rev", 0) or 0),
                    rate_limit_per_minute=getattr(
                        dispatcher, "_rate_limit_per_minute", None
                    ),
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Failed to send artifact op rejection: {e}")
            return None, None, None

        # Persist the op to the ordered ops log so it survives the relay and
        # can be replayed to late joiners. Persistence is best-effort: a
        # storage failure must never break the live fan-out, and transient op
        # types (cursor, presence, snapshot requests) are skipped. Non-existent
        # artifacts are skipped by ``append_artifact_op`` itself.
        seq: Optional[int] = None
        if artifact is not None:
            try:
                from src.core.artifacts.manager import ArtifactManager

                manager = ArtifactManager(
                    api.get_db(), config.get("artifacts", {}) or {}
                )
                seq = await asyncio.to_thread(
                    manager.append_op,
                    artifact_id,
                    op.get("op_type") or "op",
                    connection.user_id,
                    op,
                )
            except Exception as e:
                logger.warning(f"Failed to persist artifact op for {artifact_id}: {e}")

        # Relay to the artifact's other subscribers, carrying the
        # server-assigned seq. Exclude only the ORIGINATING connection, not
        # the whole user: a second tab of the same user is a distinct
        # subscriber (own per-tab client_id) and must receive the relay or
        # its optimistic state diverges until reload. The originating
        # connection is acknowledged separately below.
        try:
            await relay_artifact_op(
                dispatcher=dispatcher,
                artifact_id=artifact_id,
                op=op,
                actor_id=connection.user_id,
                exclude_connection_id=connection.connection_id,
                seq=seq,
            )
        except Exception as e:
            logger.warning(f"Failed to relay artifact op: {e}")

        # Acknowledge the actor with the assigned seq so it can commit its
        # optimistic local op (OT). Transient ops (seq is None) are skipped.
        if seq is not None:
            try:
                await send_artifact_ack(
                    connection,
                    artifact_id,
                    op,
                    seq,
                    rate_limit_per_minute=getattr(
                        dispatcher, "_rate_limit_per_minute", None
                    ),
                )
            except Exception as e:
                logger.debug(f"Failed to ack artifact op for {artifact_id}: {e}")

        return None, None, None
