"""
Channel ratchet sub-package.

Server-managed, per-channel key ratchet for message at-rest
encryption. Designed for SMB / team-chat deployments where the
server is trusted with key material but a stolen client device,
a careless export, or a compromised co-worker should not be able
to silently decrypt a deleted message.

Threat model in scope:

* a stolen phone that fetched the active ``start_key`` while the
  user was online;
* a former employee whose account was removed but who captured
  ciphertext from a backup;
* an authorized admin exporting channel data while offline and
  coming back later.

Threat model out of scope:

* active compromise of the PlexiChat server process;
* physical access to the encrypted keyring files together with
  the KEK;
* clients that intentionally keep a separate copy of ciphertext
  out of band.

Architecture:

* :class:`ChannelRatchetStore` owns the ``channel_ratchet_intervals``
  table. It never holds raw keys; it wraps them with the existing
  message keyring.
* :class:`ChannelRatchetManager` orchestrates the store, the
  keyring, and the HKDF primitive to encrypt and decrypt messages,
  rotate intervals, and split on delete.
* :mod:`hkdf` is the small wrapper around ``cryptography``'s
  HKDF-SHA256.

The wire format is ``ENC:3:{interval_id}:{base64(nonce || ct || tag)}``.
The per-message key is::

    HKDF-SHA256(
        ikm = start_key,
        salt = str(interval_id).encode(),
        info = nonce || str(message_id).encode() || CONTEXT_TAG,
        length = 32,
    )

See :mod:`src.utils.encryption.hkdf` for the wrapper and
:mod:`src.core.migrations.migrations.045_add_channel_ratchet` for
the schema.
"""

from .exceptions import (
    ChannelRatchetError,
    RatchetIntervalClosedError,
    RatchetIntervalNotFoundError,
    RatchetKeyWrapError,
    RatchetRotationDisabledError,
)
from .interval import RatchetInterval
from .manager import (
    CONTEXT_TAG,
    DEFAULT_MAX_INTERVAL_AGE_SECONDS,
    DEFAULT_MAX_MESSAGES_PER_INTERVAL,
    LEGACY_WIRE_PREFIXES,
    WIRE_PREFIX,
    WIRE_VERSION,
    ChannelRatchetManager,
    EncryptedMessageV3,
    _legacy_envelope_allowed as legacy_envelope_allowed,
)
from .notify import (
    notify_ratchet_update,
    notify_ratchet_update_async,
)
from .protocol import ChannelRatchetProtocol
from .store import ChannelRatchetStore

__all__ = [
    "ChannelRatchetError",
    "RatchetIntervalClosedError",
    "RatchetIntervalNotFoundError",
    "RatchetKeyWrapError",
    "RatchetRotationDisabledError",
    "RatchetInterval",
    "ChannelRatchetStore",
    "ChannelRatchetManager",
    "ChannelRatchetProtocol",
    "EncryptedMessageV3",
    "WIRE_VERSION",
    "WIRE_PREFIX",
    "LEGACY_WIRE_PREFIXES",
    "CONTEXT_TAG",
    "DEFAULT_MAX_MESSAGES_PER_INTERVAL",
    "DEFAULT_MAX_INTERVAL_AGE_SECONDS",
    "notify_ratchet_update",
    "notify_ratchet_update_async",
    "legacy_envelope_allowed",
]
