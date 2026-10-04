from ..models import Notification, PushPayload, MentionType


from typing import Optional

import utils.logger as logger
from src.core.events.snowflakes import stringify_snowflake_ids

from .protocol import NotificationProtocol


# Push-token at-rest cryptography.
#
# SECURITY: FCM and APNs device tokens MUST NOT be stored in
# plaintext at rest. A database compromise with plaintext tokens
# gives an attacker everything they need to push arbitrary
# notifications (including phishing content with embedded URLs) to
# every active user. We wrap tokens in an envelope that is encrypted
# with the application's existing Plexichat encryption layer (the
# same keyring-derived key that protects other sensitive blobs)
# before they hit the row, and unwrap on read.
#
# The envelope format is intentionally ASCII-safe (base64) so the
# column type can be TEXT without a schema migration:
#
#     ENC:v1:<base64(nonce|ciphertext|tag)>
#
# This matches the format used elsewhere in the codebase (see
# ``Keyring.wrap`` for the matching producer).
try:
    from src.utils.encryption import (
        encrypt_data as _push_encrypt_data,
        decrypt_data as _push_decrypt_data,
    )
except ImportError:  # pragma: no cover
    _push_encrypt_data = None
    _push_decrypt_data = None


def encrypt_push_token(token: str) -> str:
    """Encrypt a push token for at-rest storage.

    Returns the original token unchanged if the encryption layer is
    unavailable AND ``PLEXICHAT_REQUIRE_FAIL_CLOSED`` is set, in
    which case this raises — never silently downgrade.
    """
    if not token:
        return token
    if _push_encrypt_data is None:
        import os as _push_os

        if _push_os.environ.get("PLEXICHAT_REQUIRE_FAIL_CLOSED", "") not in ("", "0"):
            raise RuntimeError(
                "push-token encryption unavailable and fail-closed "
                "mode is required; refusing to store plaintext token."
            )
        # Permissions-aware fallback: log loud but proceed under
        # developer/test builds that haven't initialized
        # encryption. Production operators MUST see this in their
        # logs and remediate.
        import utils.logger as _push_logger

        _push_logger.critical(
            "push-token stored in plaintext because the "
            "encryption module is unavailable."
        )
        return f"PLAIN:{token}"
    return _push_encrypt_data(token, context="push_token")


def decrypt_push_token(stored: str) -> str:
    """Reverse of :func:`encrypt_push_token`.

    Backwards compatibility: rows written before this hardening
    landed may be PLANE TEXT. Older Plexichat deployments stored FCM /
    APNs tokens as a freeform string column with no envelope. New
    rows are written via ``encrypt_push_token`` and start with
    ``ENC:`` (the canonical envelope). On read we accept three
    formats:
        1. ``PLAIN:<token>`` — explicit degraded-mode marker.
        2. ``ENC:v1:...`` — encrypted.
        3. Anything else — treated as legacy plaintext and a
           loud warning is logged so the operator sees it. The token
           is still returned so existing functionality keeps working
           until the next migration cycle scrubs these rows.
    """
    if not stored:
        return stored
    if stored.startswith("ENC:") or stored.startswith("enc:"):
        if _push_decrypt_data is None:
            raise RuntimeError(
                "push-token decryption unavailable; refusing to handle encrypted token."
            )
        return _push_decrypt_data(stored, context="push_token")
    if stored.startswith("PLAIN:"):
        import utils.logger as _push_logger

        _push_logger.warning(
            "Decrypting a plaintext-tagged push token; the "
            "operator should re-run without PLEXICHAT_REQUIRE_FAIL_CLOSED."
        )
        return stored[len("PLAIN:") :]
    # Legacy plaintext row that was written before the encryption
    # helper existed. Return as-is but warn loudly so the operator
    # knows the row is overdue for migration.
    import utils.logger as _push_logger

    _push_logger.warning(
        "push-token row lacked ENC:v1: envelope; treating as legacy "
        "plaintext. Run the push-token migration step ASAP to "
        "re-envelope this row."
    )
    return stored


class PushMixin(NotificationProtocol):
    # Upper bound on devices fanned out per notification batch so a single
    # @everyone mention in a huge server cannot stall the delivery worker.
    PUSH_DELIVERY_BATCH_LIMIT = 100

    def _get_push_manager(self):
        """Return the initialized push module manager, or None.

        Fail-open: when the push module was never set up (tests, embedded
        deployments, push disabled) notification creation proceeds without
        device delivery.
        """
        try:
            from src.core import push as push_module

            if (
                getattr(push_module, "_setup_complete", False)
                and push_module._manager is not None
            ):
                return push_module._manager
        except Exception as e:
            logger.debug(f"Push module unavailable for delivery: {e}")
        return None

    def deliver_push_notifications(self, notifications) -> int:
        """Best-effort mobile/web push delivery for created notifications.

        Builds each payload via :meth:`prepare_push_payload` and feeds it
        into ``PushManager.send_push``. Every failure is logged, never
        raised — push must not affect the message-send path.
        """
        manager = self._get_push_manager()
        if manager is None:
            return 0

        sent = 0
        for notification in notifications[: self.PUSH_DELIVERY_BATCH_LIMIT]:
            try:
                if notification is None:
                    continue
                payload = self.prepare_push_payload(notification)
                if payload is None:
                    continue
                sent += manager.send_push(
                    user_id=payload.user_id,
                    title=payload.title,
                    body=payload.body,
                    data=dict(payload.data or {}),
                    badge=payload.badge_count,
                    sound=payload.sound,
                    priority=payload.priority,
                )
            except Exception as e:
                logger.warning(
                    f"Push delivery failed for notification "
                    f"{getattr(notification, 'id', '?')}: {e}"
                )
        return sent

    def _deliver_push_safely(self, notifications) -> None:
        try:
            self.deliver_push_notifications(notifications)
        except Exception as e:
            logger.warning(f"Push delivery worker failed: {e}")

    def _queue_push_delivery(self, notifications) -> None:
        """Schedule push delivery onto the shared bounded background queue.

        A single long-lived consumer thread drains pending batches (see
        ``notifications.background``), so bursts of notifications can never
        spawn unbounded threads. No-op unless the push module is
        initialized, so tests and deployments without push never enqueue.
        """
        if not notifications:
            return
        if self._get_push_manager() is None:
            return
        try:
            from ..background import (
                get_notification_dispatcher,
                is_sync_notification_dispatch,
            )

            batch = list(notifications)[: self.PUSH_DELIVERY_BATCH_LIMIT]

            def _deliver_batch():
                self._deliver_push_safely(batch)

            if is_sync_notification_dispatch():
                _deliver_batch()
                return
            if not get_notification_dispatcher().submit(_deliver_batch):
                logger.debug(
                    "Notification dispatch queue full; dropped push batch "
                    f"of {len(batch)} notification(s)"
                )
        except Exception as e:
            logger.debug(f"Failed to schedule push delivery: {e}")

    def shutdown_push_delivery(self, timeout: Optional[float] = None) -> None:
        """Flush queued push batches and join the consumer thread."""
        try:
            from ..background import (
                SHUTDOWN_DRAIN_TIMEOUT,
                get_notification_dispatcher,
            )

            get_notification_dispatcher().drain(
                SHUTDOWN_DRAIN_TIMEOUT if timeout is None else timeout
            )
        except Exception as e:
            logger.debug(f"Push delivery shutdown drain failed: {e}")

    def prepare_push_payload(self, notification: Notification) -> PushPayload:
        sender_name = "Someone"
        row = self._db.fetch_one(
            "SELECT username FROM auth_users WHERE id = ?", (notification.author_id,)
        )
        if row:
            sender_name = row["username"]

        if notification.mention_type == MentionType.USER:
            title = f"{sender_name} mentioned you"
        elif notification.mention_type == MentionType.ROLE:
            title = f"{sender_name} mentioned your role"
        elif notification.mention_type == MentionType.EVERYONE:
            title = f"{sender_name} mentioned @everyone"
        elif notification.mention_type == MentionType.HERE:
            title = f"{sender_name} mentioned @here"
        else:
            title = f"New mention from {sender_name}"

        unread = self.get_unread_count(notification.user_id)

        # Ids must cross to the device as decimal strings. FCM and APNs
        # coerce ``data`` values with ``str(...)``, but the Web Push path
        # ``json.dumps`` the dict verbatim, so a numeric snowflake would be
        # handed to a browser service worker as a JSON number and rounded
        # past 2**53 — addressing the wrong message/channel when the
        # notification is tapped. Stringify centrally so every platform
        # agrees; ``mention_type`` is left as its string value.
        return PushPayload(
            user_id=notification.user_id,
            title=title,
            body=notification.content_preview,
            data=stringify_snowflake_ids(
                {
                    "notification_id": notification.id,
                    "message_id": notification.message_id,
                    "conversation_id": notification.conversation_id,
                    "server_id": notification.server_id,
                    "channel_id": notification.channel_id,
                    "thread_id": notification.thread_id,
                    "mention_type": notification.mention_type.value,
                }
            ),
            badge_count=unread.mention_count,
            sound="default",
            priority="high",
        )
