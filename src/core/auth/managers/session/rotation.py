"""Session-id rotation at privilege / credential transitions.

A session created *before* a security-bearing transition (2FA enable/disable,
email change, password change, ...) is an authentication credential an attacker
may already know: a bearer token stolen from a password-only session keeps
working after the victim adds a second factor unless the session identifier is
regenerated. Revoking other sessions is not enough -- the current one is
usually preserved so the legitimate user is not logged out, and that preserved
id is exactly the fixated/stale credential.

:meth:`RotationMixin.rotate_session_id` revokes the supplied session and mints
a brand-new one (new id **and** new secret), carrying over the device and the
blind-indexed IP/User-Agent binding so the user keeps a working, equally bound
session while the old id/token stops authenticating immediately.
"""

from typing import Optional

from ...exceptions import TokenInvalidError
from ...models import Session
from src.core.database import invalidate_pattern
from ..protocol import AuthManagerProtocol


class RotationMixin(AuthManagerProtocol):
    def rotate_session_id(self, user_id: int, old_session_id: int) -> Session:
        """Invalidate ``old_session_id`` and return a freshly minted session.

        The new session gets a new snowflake id and a new random token secret;
        it inherits the old session's device id and (decrypted) IP/User-Agent so
        token binding and trusted-device expiry keep working. The old session is
        revoked through :meth:`revoke_session`, which also drops the verify
        cache and closes any gateway socket bound to it.

        Fails closed with :class:`TokenInvalidError` when the supplied id does
        not name a live session owned by ``user_id`` -- a caller cannot rotate
        (and thereby keep alive) somebody else's or already-dead session.
        """
        row = self._db.fetch_one(
            "SELECT * FROM auth_sessions WHERE id = ? AND user_id = ?",
            (old_session_id, user_id),
        )
        if not row or row.get("revoked"):
            raise TokenInvalidError("Session no longer valid")

        # Recover the plaintext binding context from the old row. Its encrypted
        # blobs are keyed with the OLD id as the AEAD context, so decrypt using
        # that id and then re-encrypt under the new one inside _create_session.
        ip_address: Optional[str] = None
        if row.get("ip_encrypted"):
            try:
                ip_address = self.crypto.decrypt_data(
                    row["ip_encrypted"], context=str(old_session_id)
                )
            except Exception:
                ip_address = None
        user_agent: Optional[str] = None
        if row.get("ua_encrypted"):
            try:
                user_agent = self.crypto.decrypt_data(
                    row["ua_encrypted"], context=str(old_session_id)
                )
            except Exception:
                user_agent = None

        # Revoke first: on any failure below the user has no stale session left
        # (fail closed), and a fresh login mints a brand-new id anyway.
        self.revoke_session(user_id, old_session_id)
        invalidate_pattern("token_verify:*")

        return self._create_session(
            user_id, row.get("device_id"), ip_address, user_agent
        )
