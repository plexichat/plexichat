"""
Artifact handlers - Handle artifact real-time fabric opcodes.

Registers handlers for:

- ``ARTIFACT_SUBSCRIBE`` (60): register the connection's user as a subscriber
  of an artifact, enforcing RBAC via ``artifact.view`` permission or
  conversation membership before granting access.
- ``ARTIFACT_UNSUBSCRIBE`` (61): remove the subscription.
- ``ARTIFACT_OP`` (62): validate the op payload shape and relay the op to the
  artifact's other subscribers. No persistence happens here.

Delivery reuses the existing dispatcher send path and the per-connection rate
limit (no new rate-limit mechanism is introduced).
"""

from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

import src.api as api
import utils.logger as logger

from src.api.websocket.opcodes import GatewayCloseCode
from src.api.websocket.connection import Connection
from src.api.websocket.artifacts import (
    get_artifact_subscription_registry,
    relay_artifact_op,
    send_artifact_sync,
)
from src.core.artifacts.repository import get_artifact

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

    async def handle_artifact_subscribe(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
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
        if artifact is not None:
            server_id = artifact.server_id
            conversation_id = artifact.conversation_id
            if server_id is not None:
                servers_mod = api.get_servers()
                if servers_mod is not None:
                    from src.core.servers.exceptions import PermissionDeniedError

                    try:
                        servers_mod.require_permission(
                            connection.user_id, server_id, "artifact.view"
                        )
                    except PermissionDeniedError:
                        logger.debug(
                            f"User {connection.user_id} denied artifact.view "
                            f"for artifact {artifact_id}"
                        )
                        return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)
            elif conversation_id is not None:
                messaging_mod = api.get_messaging()
                if messaging_mod is not None:
                    try:
                        if not messaging_mod.is_participant(
                            conversation_id, connection.user_id
                        ):
                            logger.debug(
                                f"User {connection.user_id} not a participant of "
                                f"conversation {conversation_id} for artifact {artifact_id}"
                            )
                            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)
                    except Exception as exc:
                        logger.debug(
                            f"Membership check failed for artifact {artifact_id}: {exc}"
                        )
                        return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)
            else:
                # Personal / notes scope: only the author may subscribe.
                if connection.user_id != artifact.author_id:
                    logger.debug(
                        f"User {connection.user_id} is not the author of "
                        f"personal artifact {artifact_id}"
                    )
                    return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        self._registry.subscribe(connection.user_id, artifact_id)
        logger.debug(f"User {connection.user_id} subscribed to artifact {artifact_id}")

        snapshot = artifact.payload if artifact else {"error": "not_found"}
        try:
            await send_artifact_sync(connection, artifact_id, snapshot)  # type: ignore[arg-type]
        except Exception as e:
            logger.debug(f"Failed to send artifact sync: {e}")

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

        self._registry.unsubscribe(connection.user_id, artifact_id)
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

        if op.get("op_type") is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            artifact_id = int(artifact_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid artifact_id type in op: {type(artifact_id)}")
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        # Relay to other subscribers. Persistence is the responsibility of a
        # later group (editor / ops persistence). Here we only fan out.
        try:
            await relay_artifact_op(
                dispatcher=dispatcher,
                artifact_id=artifact_id,
                op=op,
                actor_id=connection.user_id,
                exclude_user_id=connection.user_id,
            )
        except Exception as e:
            logger.warning(f"Failed to relay artifact op: {e}")

        return None, None, None
