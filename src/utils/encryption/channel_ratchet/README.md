# Channel Ratchet Sub-Package

## Purpose

Server-managed, per-channel message encryption for PlexiChat. The
ratchet protects historical channel content from being silently
re-decrypted after a message is hard-deleted, even by a client
device that previously fetched the active ratchet key.

## Threat Model

In scope:

* A stolen phone that pulled the active ratchet key while the
  user was authenticated, then went offline.
* A former employee whose account was removed but who exported
  ciphertext out of band.
* An authorized admin who copies channel content during a session
  and tries to decrypt it after a deletion.

Out of scope (this is server-orchestrated, not E2EE):

* Active compromise of the PlexiChat server process.
* Physical access to the keyring file plus the KEK.
* Clients that keep a separate out-of-band copy of ciphertext.

## Architecture

| File | Component | Responsibility |
|------|-----------|----------------|
| `protocol.py` | `ChannelRatchetProtocol` | Structural type for pyright across the store / manager boundary |
| `exceptions.py` | `ChannelRatchetError` and friends | Custom exception hierarchy |
| `interval.py` | `RatchetInterval` | Value object for a single key range |
| `store.py` | `ChannelRatchetStore` | Persistence for `channel_ratchet_intervals` (wraps `start_key` via the existing keyring) |
| `manager.py` | `ChannelRatchetManager` | Encrypt, decrypt, rotate, split, snapshot |
| `hkdf.py` (parent) | `derive_key` | HKDF-SHA256 wrapper |

## Wire Format

```
ENC:3:{interval_id}:{base64(nonce || ciphertext_with_tag)}
```

* `interval_id` is the snowflake id of the row in
  `channel_ratchet_intervals`.
* `nonce` is 12 random bytes, unique per message. The nonce is
  the actual deletion primitive: it is destroyed with the row,
  so the ciphertext cannot be re-derived from the start key
  alone.
* `ciphertext_with_tag` is the AES-256-GCM output for the
  derived per-message key with AAD
  `b"ENC:3:" + interval_id_bytes`.

The per-message key is derived as:

```
key = HKDF-SHA256(
    ikm  = interval.start_key,
    salt = str(interval_id).encode(),
    info = nonce || str(message_id).encode() || b"plexichat.channel-message.v3",
    length = 32,
)
```

## Rotation Cadence

* New interval after `messaging.ratchet_max_messages` (default
  1000) messages in the current interval.
* New interval after `messaging.ratchet_max_age_seconds`
  (default 86400 seconds = 24 hours) since the current interval
  was opened.
* Whichever threshold is hit first wins. The check is performed
  inside `send_message` after a successful insert.

## Split On Delete

When the license feature `channel_ratchet_encryption` is enabled
(default for paid tiers) and `messaging.ratchet_split_on_delete`
is true, a hard delete:

1. Closes the currently active interval at
   `end_message_id = deleted_message_id + 1`.
2. Opens a new interval with a freshly generated `start_key`.

The deleted message's nonce is destroyed by the row delete, so
the split is defense-in-depth: the new key protects everything
forward of the deletion, and the deleted ciphertext cannot be
re-derived even with the old key.

## Usage

```python
from src.utils.encryption.channel_ratchet import ChannelRatchetManager

manager = ChannelRatchetManager(db)
result = manager.encrypt(conversation_id=42, message_id=msg_id, plaintext=b"hello")
# result.envelope -> "ENC:3:1234567890:AbCdEf==..."

plaintext = manager.decrypt(conversation_id=42, message_id=msg_id, envelope=result.envelope)
# plaintext -> b"hello"
```

The manager is exposed at the API level via
`GET /api/channels/{channel_id}/ratchet` which returns the
active interval (id, start/end message ids, base64-wrapped
`start_key`, and the context tag). The endpoint is gated by the
license feature `channel_ratchet_encryption`.
