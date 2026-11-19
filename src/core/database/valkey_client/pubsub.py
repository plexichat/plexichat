import threading
from collections import OrderedDict
from typing import Any, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError


class PubSubMixin(ValkeyClientBase):
    """Pub/sub commands, in blocking and awaitable form.

    ``try_get_pubsub_message`` is non-blocking on *both* GLIDE clients
    (it only drains a local buffer), so the awaitable form exists for
    call-site symmetry rather than because it does I/O.

    Scaling / reliability invariants
    --------------------------------
    * The set of subscribed channels is tracked locally and hard-bounded by
      ``_MAX_SUBSCRIPTIONS``. Valkey/GLIDE keeps its own subscription set, but
      without a local ceiling a caller that subscribes to dynamic (per-user /
      per-room) channels grows it without limit — inflating client memory and
      the fan-out of every published message. The cap fails closed (refused
      channels are counted and logged, never sent to the client).
    * Valkey does NOT persist pub/sub subscriptions across a reconnect. The
      local registry is the source of truth used by :meth:`resubscribe` (wired
      into ``connect`` / ``aconnect``) to restore them after the client is
      recreated, so a dropped connection does not silently turn the listener
      deaf.
    * Duplicate subscribe calls are deduped against the registry: a channel
      already tracked is never re-sent to the client.
    """

    # Hard ceiling on concurrently tracked subscriptions. Sized for a single
    # Plexichat instance; raise deliberately if a deployment needs headroom.
    _MAX_SUBSCRIPTIONS = 1024

    _listening: bool = False

    # --- bounded subscription registry ----------------------------------

    def _ensure_subscription_state(self) -> None:
        """Lazily create the registry + lock.

        Lazy (rather than in ``__init__``) so the mixin composes safely with
        ``ValkeyClientBase`` and any future mixin that reorders or omits a
        cooperative ``super().__init__()`` call.
        """
        if getattr(self, "_subscriptions", None) is None:
            self._subscriptions: "OrderedDict[str, None]" = OrderedDict()
            self._subscription_lock = threading.Lock()
            # Observability: channels refused because the cap was reached.
            self._subscription_drops = 0

    def _channel_set(self, channels: Any) -> set:
        return {self._prefixed_key(self._sanitize_key(c)) for c in channels}

    def _plan_subscribe_locked(self, full_channels: set) -> list:
        """Return the subset of ``full_channels`` that fits under the cap.

        Caller MUST hold ``self._subscription_lock``. Deterministic (sorted)
        so the accepted set is stable under concurrent callers. Channels that
        do not fit are counted as drops and never reach the client — fail
        closed rather than silently growing the registry past the ceiling.
        """
        if not full_channels:
            return []
        new = sorted(c for c in full_channels if c not in self._subscriptions)
        capacity = self._MAX_SUBSCRIPTIONS - len(self._subscriptions)
        if capacity <= 0:
            if new:
                self._subscription_drops += len(new)
                logger.warning(
                    "Pub/sub subscription cap (%d) reached; refusing %d channel(s)",
                    self._MAX_SUBSCRIPTIONS,
                    len(new),
                )
            return []
        accepted = new[:capacity]
        if len(new) > capacity:
            dropped = len(new) - capacity
            self._subscription_drops += dropped
            logger.warning(
                "Pub/sub subscription cap (%d) reached; refusing %d of %d new "
                "channel(s)",
                self._MAX_SUBSCRIPTIONS,
                dropped,
                len(new),
            )
        return accepted

    # --- reconnection hooks ---------------------------------------------

    def connect(self) -> bool:
        """Connect, then restore subscriptions tracked before a connection drop."""
        ok = super().connect()
        if ok and getattr(self, "_subscriptions", None):
            try:
                self.resubscribe()
            except Exception as e:
                # A failed resubscribe must not mask a successful connection;
                # surface it loudly so operators can see the degraded state.
                logger.warning("Pub/sub resubscribe after reconnect failed: %s", e)
        return ok

    async def aconnect(self) -> bool:
        """Async counterpart of :meth:`connect`."""
        ok = await super().aconnect()
        if ok and getattr(self, "_subscriptions", None):
            try:
                await self.aresubscribe()
            except Exception as e:
                logger.warning(
                    "Pub/sub async resubscribe after reconnect failed: %s", e
                )
        return ok

    # --- publish ---------------------------------------------------------

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

    # --- subscribe / unsubscribe -----------------------------------------

    def subscribe(self, *channels: str) -> Any:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_channels = self._channel_set(channels)
        if not full_channels:
            return self._pubsub_handle

        self._ensure_subscription_state()
        with self._subscription_lock:
            accepted = self._plan_subscribe_locked(full_channels)
            if not accepted:
                return self._pubsub_handle

        try:
            client.subscribe(set(accepted))
        except Exception as e:
            logger.error(f"GLIDE SUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"SUBSCRIBE failed: {e}")

        # Only record channels the client actually accepted, so a failed call
        # never leaves the registry out of sync with the real subscription.
        with self._subscription_lock:
            for c in accepted:
                self._subscriptions[c] = None
            self._listening = True
        logger.debug(f"GLIDE SUBSCRIBE: {accepted}")
        return self._pubsub_handle

    async def asubscribe(self, *channels: str) -> Any:
        aclient = self._ensure_aconnected()
        full_channels = self._channel_set(channels)
        if not full_channels:
            return self._pubsub_handle

        self._ensure_subscription_state()
        with self._subscription_lock:
            accepted = self._plan_subscribe_locked(full_channels)
            if not accepted:
                return self._pubsub_handle

        try:
            await aclient.subscribe(set(accepted))
        except Exception as e:
            logger.error(f"GLIDE SUBSCRIBE (async) failed: {e}")
            raise ValkeyOperationError(f"SUBSCRIBE failed: {e}")

        with self._subscription_lock:
            for c in accepted:
                self._subscriptions[c] = None
            self._listening = True
        logger.debug(f"GLIDE SUBSCRIBE (async): {accepted}")
        return self._pubsub_handle

    def unsubscribe(self, *channels: str) -> None:
        self._ensure_subscription_state()
        with self._subscription_lock:
            if not self._listening and not self._subscriptions:
                return
            if channels:
                full_channels = self._channel_set(channels)
                to_remove = set(full_channels) & set(self._subscriptions)
            else:
                full_channels = None
                to_remove = set(self._subscriptions)

        client = self._client
        if not client:
            return

        try:
            if channels:
                # Unsubscribe exactly what the caller asked for on the client;
                # prune only the subset we were tracking from the registry.
                client.unsubscribe(full_channels)
            else:
                # Valkey semantics: no channels == unsubscribe from everything.
                client.unsubscribe()
        except Exception as e:
            logger.error(f"GLIDE UNSUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"UNSUBSCRIBE failed: {e}")

        with self._subscription_lock:
            for c in to_remove:
                self._subscriptions.pop(c, None)
            if not self._subscriptions:
                self._listening = False
        logger.debug(f"GLIDE UNSUBSCRIBE: {channels or 'all'}")

    async def aunsubscribe(self, *channels: str) -> None:
        self._ensure_subscription_state()
        with self._subscription_lock:
            if not self._listening and not self._subscriptions:
                return
            if channels:
                full_channels = self._channel_set(channels)
                to_remove = set(full_channels) & set(self._subscriptions)
            else:
                full_channels = None
                to_remove = set(self._subscriptions)

        aclient = self._aclient
        if not aclient:
            return

        try:
            if channels:
                await aclient.unsubscribe(full_channels)
            else:
                await aclient.unsubscribe()
        except Exception as e:
            logger.error(f"GLIDE UNSUBSCRIBE (async) failed: {e}")
            raise ValkeyOperationError(f"UNSUBSCRIBE failed: {e}")

        with self._subscription_lock:
            for c in to_remove:
                self._subscriptions.pop(c, None)
            if not self._subscriptions:
                self._listening = False
        logger.debug(f"GLIDE UNSUBSCRIBE (async): {channels or 'all'}")

    # --- reconnect / observability --------------------------------------

    def resubscribe(self) -> int:
        """Re-apply all locally-tracked subscriptions on the current client.

        Pub/sub subscriptions are connection-scoped: after ``connect()``
        recreates the client the server knows nothing about them. Without this
        call the listener silently stops receiving messages while
        ``_listening`` still reads ``True``. Returns the number of channels
        re-applied (0 when nothing was tracked).
        """
        self._ensure_connected()
        client = self._client
        assert client is not None
        self._ensure_subscription_state()
        with self._subscription_lock:
            channels = list(self._subscriptions.keys())
        if not channels:
            return 0
        try:
            client.subscribe(set(channels))
        except Exception as e:
            logger.error(f"GLIDE RESUBSCRIBE failed: {e}")
            raise ValkeyOperationError(f"RESUBSCRIBE failed: {e}")
        self._listening = True
        logger.info("Pub/sub resubscribed to %d channel(s)", len(channels))
        return len(channels)

    async def aresubscribe(self) -> int:
        """Async counterpart of :meth:`resubscribe`."""
        aclient = self._ensure_aconnected()
        self._ensure_subscription_state()
        with self._subscription_lock:
            channels = list(self._subscriptions.keys())
        if not channels:
            return 0
        try:
            await aclient.subscribe(set(channels))
        except Exception as e:
            logger.error(f"GLIDE RESUBSCRIBE (async) failed: {e}")
            raise ValkeyOperationError(f"RESUBSCRIBE failed: {e}")
        self._listening = True
        logger.info("Pub/sub async resubscribed to %d channel(s)", len(channels))
        return len(channels)

    def get_subscription_metrics(self) -> dict:
        """Expose the bounded registry state for health/dashboards."""
        self._ensure_subscription_state()
        with self._subscription_lock:
            return {
                "subscriptions": len(self._subscriptions),
                "max": self._MAX_SUBSCRIPTIONS,
                "drops": self._subscription_drops,
                "listening": self._listening,
            }

    # --- message drain ---------------------------------------------------

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
