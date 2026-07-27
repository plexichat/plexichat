from typing import Any, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError


class PubSubMixin(ValkeyClientBase):
    _listening: bool = False

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

    def subscribe(self, *channels: str) -> Any:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_channels = {self._prefixed_key(self._sanitize_key(c)) for c in channels}

        try:
            client.subscribe(full_channels)
            self._listening = True
            logger.debug(f"GLIDE SUBSCRIBE: {channels}")
            return self._pubsub_handle
        except Exception as e:
            logger.error(f"GLIDE SUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"SUBSCRIBE failed: {e}")

    def unsubscribe(self, *channels: str) -> None:
        if not self._listening:
            return

        client = self._client
        if not client:
            return

        full_channels = {self._prefixed_key(self._sanitize_key(c)) for c in channels}

        try:
            client.unsubscribe(full_channels)
            if not channels:
                self._listening = False
            logger.debug(f"GLIDE UNSUBSCRIBE: {channels}")
        except Exception as e:
            logger.error(f"GLIDE UNSUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"UNSUBSCRIBE failed: {e}")

    def get_pubsub_message(self) -> Optional[Any]:
        client = self._client
        if not client:
            return None
        try:
            return client.get_pubsub_message()
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
