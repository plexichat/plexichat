"""Pub/Sub operations mixin."""

from typing import Any

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError


class PubSubMixin(RedisClientBase):
    """Mixin providing publish/subscribe operations."""

    def publish(self, channel: str, message: str) -> int:
        """
        Publish a message to a channel.

        Args:
            channel: Channel name.
            message: Message to publish.

        Returns:
            Number of subscribers that received the message.
        """
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_channel = self._prefixed_key(self._sanitize_key(channel))

        try:
            count = int(client.publish(full_channel, message))
            logger.debug(f"Redis PUBLISH: {channel} -> {count} subscribers")
            return count
        except Exception as e:
            logger.error(f"Redis PUBLISH failed for {channel}: {e}")
            raise RedisOperationError(f"PUBLISH failed: {e}")

    def subscribe(self, *channels: str) -> Any:
        """
        Subscribe to channels.

        Args:
            channels: Channel names to subscribe to.

        Returns:
            PubSub object for listening to messages.
        """
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_channels = [self._prefixed_key(self._sanitize_key(c)) for c in channels]

        try:
            if not self._pubsub:
                self._pubsub = client.pubsub()
            self._pubsub.subscribe(*full_channels)
            logger.debug(f"Redis SUBSCRIBE: {channels}")
            return self._pubsub
        except Exception as e:
            logger.error(f"Redis SUBSCRIBE failed: {e}")
            raise RedisOperationError(f"SUBSCRIBE failed: {e}")

    def unsubscribe(self, *channels: str) -> None:
        """Unsubscribe from channels."""
        if not self._pubsub:
            return

        full_channels = [self._prefixed_key(self._sanitize_key(c)) for c in channels]

        try:
            self._pubsub.unsubscribe(*full_channels)
            logger.debug(f"Redis UNSUBSCRIBE: {channels}")
        except Exception as e:
            logger.error(f"Redis UNSUBSCRIBE failed: {e}")
            raise RedisOperationError(f"UNSUBSCRIBE failed: {e}")
