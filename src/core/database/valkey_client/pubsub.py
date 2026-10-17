from typing import Any, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError


class PubSubMixin(ValkeyClientBase):
    """Pub/sub commands, in blocking and awaitable form.

    ``try_get_pubsub_message`` is non-blocking on *both* GLIDE clients
    (it only drains a local buffer), so the awaitable form exists for
    call-site symmetry rather than because it does I/O.
    """

    _listening: bool = False

    def _channel_set(self, channels: Any) -> set:
        return {self._prefixed_key(self._sanitize_key(c)) for c in channels}

    def publish(self, channel: str, message: str) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_channel = self._prefixed_key(self._sanitize_key(channel))

        try:
            count = int(client.publish(message, full_channel))
            logger.debug(f"GLIDE PUBLISH: {channel} -> {count} subscribers")
            return count
        except Exception as e:
            logger.error(f"GLIDE PUBLISH failed for {channel}: {e}")
            raise ValkeyOperationError(f"PUBLISH failed: {e}")

    async def apublish(self, channel: str, message: str) -> int:
        aclient = self._ensure_aconnected()
        full_channel = self._prefixed_key(self._sanitize_key(channel))

        try:
            count = int(await aclient.publish(message, full_channel))
            logger.debug(f"GLIDE PUBLISH (async): {channel} -> {count} subscribers")
            return count
        except Exception as e:
            logger.error(f"GLIDE PUBLISH (async) failed for {channel}: {e}")
            raise ValkeyOperationError(f"PUBLISH failed: {e}")

    def subscribe(self, *channels: str) -> Any:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_channels = self._channel_set(channels)

        try:
            client.subscribe(full_channels)
            self._listening = True
            logger.debug(f"GLIDE SUBSCRIBE: {channels}")
            return self._pubsub_handle
        except Exception as e:
            logger.error(f"GLIDE SUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"SUBSCRIBE failed: {e}")

    async def asubscribe(self, *channels: str) -> Any:
        aclient = self._ensure_aconnected()
        full_channels = self._channel_set(channels)

        try:
            await aclient.subscribe(full_channels)
            self._listening = True
            logger.debug(f"GLIDE SUBSCRIBE (async): {channels}")
            return self._pubsub_handle
        except Exception as e:
            logger.error(f"GLIDE SUBSCRIBE (async) failed: {e}")
            raise ValkeyOperationError(f"SUBSCRIBE failed: {e}")

    def unsubscribe(self, *channels: str) -> None:
        if not self._listening:
            return

        client = self._client
        if not client:
            return

        full_channels = self._channel_set(channels)

        try:
            client.unsubscribe(full_channels)
            if not channels:
                self._listening = False
            logger.debug(f"GLIDE UNSUBSCRIBE: {channels}")
        except Exception as e:
            logger.error(f"GLIDE UNSUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"UNSUBSCRIBE failed: {e}")

    async def aunsubscribe(self, *channels: str) -> None:
        if not self._listening:
            return

        aclient = self._aclient
        if not aclient:
            return

        full_channels = self._channel_set(channels)

        try:
            await aclient.unsubscribe(full_channels)
            if not channels:
                self._listening = False
            logger.debug(f"GLIDE UNSUBSCRIBE (async): {channels}")
        except Exception as e:
            logger.error(f"GLIDE UNSUBSCRIBE (async) failed: {e}")
            raise ValkeyOperationError(f"UNSUBSCRIBE failed: {e}")

    def get_pubsub_message(self) -> Optional[Any]:
        client = self._client
        if not client:
            return None
        try:
            return client.get_pubsub_message()
        except Exception:
            return None

    async def aget_pubsub_message(self) -> Optional[Any]:
        aclient = self._aclient
        if not aclient:
            return None
        try:
            return await aclient.get_pubsub_message()
        except Exception:
            return None

    def try_get_pubsub_message(self) -> Optional[Any]:
        client = self._client
        if not client:
            return None
        try:
            return client.try_get_pubsub_message()
        except Exception:
            return None

    async def atry_get_pubsub_message(self) -> Optional[Any]:
        aclient = self._aclient
        if not aclient:
            return None
        try:
            # Non-blocking on the async client too -- no await.
            return aclient.try_get_pubsub_message()
        except Exception:
            return None
