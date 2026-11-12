"""Composed mediasoup WebSocket (protoo) SFU adapter."""

import utils.logger as logger

from .connection import ConnectionMixin
from .recording import RecordingMixin
from .rooms import RoomsMixin


class MediasoupWSAdapter(ConnectionMixin, RoomsMixin, RecordingMixin):
    """WebSocket-based adapter for the mediasoup-demo server.

    Uses the protoo protocol for signaling. Each responsibility lives in its
    own mixin; ``close`` is defined here because it drives recording teardown
    *and* the signaling sockets.
    """

    async def close(self) -> None:
        """Stop active recordings and close all signaling connections."""
        for room_id in list(self._recordings):
            try:
                await self.stop_recording(room_id)
            except Exception as exc:
                logger.debug(
                    "Failed to stop mediasoup WebSocket recording for %s: %s",
                    room_id,
                    exc,
                )
            # Retry retained partial state, including unexpected exceptions,
            # before the signaling connection is closed.
            if room_id in self._recordings:
                try:
                    await self.stop_recording(room_id)
                except Exception as exc:
                    logger.debug(
                        "Retry failed to stop mediasoup WebSocket recording for %s: %s",
                        room_id,
                        exc,
                    )
            if room_id in self._recordings:
                # The connection will be closed below, so this state is no
                # longer retryable. Preserve an explicit diagnostic instead.
                self._record_recording_teardown_failure(
                    room_id, "adapter closed before recorder teardown completed"
                )

        # Cancel receiver tasks before closing the sockets they are iterating.
        receiver_tasks = getattr(self, "_receiver_tasks", {})
        for task in list(receiver_tasks.values()):
            if not task.done():
                task.cancel()
        receiver_tasks.clear()

        for key in list(self._connections.keys()):
            connection = self._connections.pop(key, None)
            if connection and connection.websocket:
                try:
                    await connection.websocket.close()
                except Exception:
                    pass
        self._connections.clear()
        self._rooms.clear()
        self._message_handlers.clear()
