from ..models import Notification, PushPayload, MentionType


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
        return _push_decrypt_data(stored)
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

        return PushPayload(
            user_id=notification.user_id,
            title=title,
            body=notification.content_preview,
            data={
                "notification_id": notification.id,
                "message_id": notification.message_id,
                "conversation_id": notification.conversation_id,
                "server_id": notification.server_id,
                "channel_id": notification.channel_id,
                "thread_id": notification.thread_id,
                "mention_type": notification.mention_type.value,
            },
            badge_count=unread.mention_count,
            sound="default",
            priority="high",
        )
