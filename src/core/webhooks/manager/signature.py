"""
Webhook payload signing using Ed25519.
"""

import base64
import hashlib
import hmac
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Dict, Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.webhooks.canonical import canonical_payload_bytes
from src.core.webhooks.exceptions import (
    PermissionDeniedError,
    WebhookNotFoundError,
)
from src.utils.encryption import decrypt_data, encrypt_data, generate_key_pair

from .constants import (
    MAX_SIGNATURE_HEADER_LENGTH,
    MAX_TIMESTAMP_HEADER_LENGTH,
    SIGNATURE_HEADER,
    SIGNATURE_TIMESTAMP_HEADER,
    SIGNATURE_VERSION,
)
from .base import WebhookManagerTrait

# Maximum clock skew tolerated on inbound webhook timestamp validation.
# 5 minutes is a reasonable upper bound that catches obvious replays
# without rejecting legitimately retried deliveries from slow proxies.
WEBHOOK_REPLAY_WINDOW_SECONDS = 5 * 60

# A captured (signature, timestamp) pair stays valid for up to the full
# replay window after capture. It can be FIRST accepted as early as
# ``timestamp - window`` (a signer whose clock leads the verifier by the full
# skew), yet the signature is still accepted at ``timestamp + window`` because
# the skew check uses ``>`` and skew == window passes. The marker may therefore
# be written a full window BEFORE the signature's timestamp, giving it a total
# exposure of 2x the window from the moment it is stored. A TTL of exactly 2x
# the window expires at that very last valid instant, so a replay landing on
# the boundary finds an expired marker and is accepted twice. Keep an explicit
# margin so the marker always strictly outlives the pair's remaining validity.
_REPLAY_TTL_MARGIN_SECONDS = 60
_REPLAY_CACHE_TTL_SECONDS = (
    WEBHOOK_REPLAY_WINDOW_SECONDS * 2 + _REPLAY_TTL_MARGIN_SECONDS
)

# An Ed25519 signature is exactly 64 bytes. Checking the length in constant
# time before the public-key operation rejects malformed/oversized headers
# without leaking which byte differed.
ED25519_SIGNATURE_LENGTH = 64
_REPLAY_MARKER = "1"


# cache_get() / cache_set() are two separate, non-atomic operations. Without a
# guard, two threads handling the SAME captured (signature, timestamp) pair can
# both observe a cache miss, both verify successfully, and both be accepted
# inside the replay window -- a replay bypass under concurrency. This lock
# makes the "already seen?" check and the "mark as seen" store a single atomic
# critical section for every delivery thread in this process. Multi-process
# deployments still rely on the shared cache tier for cross-process exclusion;
# this closes the in-process race that the default in-memory fallback exposes.
#
# The same lock serializes rotate_signing_key()'s key swap against the public
# key fetch + Ed25519 verification below. The atomic two-column UPDATE alone is
# not enough: a rotation committing between the fetch and the verify would let
# a signature made with the RETIRED key be accepted after the key had already
# changed. Holding the lock from fetch through verification closes that TOCTOU.
#
# The guard is PER WEBHOOK, not process-global. Verification holds the lock
# across two Valkey round trips (replay lookup + claim/write) and an Ed25519
# verify; a single global mutex made every inbound delivery in the process wait
# behind the slowest delivery for an unrelated webhook. A rotation only swaps
# one webhook's key and a verification only reads one webhook's key, so keying
# by webhook_id preserves the fetch/check/verify/consume atomicity exactly while
# letting independent webhooks be verified concurrently. The registry is bounded
# and refcounted so idle locks are reclaimed but a lock with a live guard is
# never evicted (evicting it would let a second thread use a different mutex).
class _WebhookSignatureLock:
    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.refs = 0


_SIGNATURE_LOCKS_GUARD = threading.Lock()
_SIGNATURE_LOCKS: "OrderedDict[int, _WebhookSignatureLock]" = OrderedDict()
_SIGNATURE_LOCKS_MAX = 1024


def _prune_signature_locks_locked() -> None:
    """Evict idle LRU entries; never evict a lock that has a live guard."""
    while len(_SIGNATURE_LOCKS) > _SIGNATURE_LOCKS_MAX:
        for key, entry in _SIGNATURE_LOCKS.items():
            if entry.refs == 0:
                del _SIGNATURE_LOCKS[key]
                break
        else:
            break  # every retained lock is in use


@contextmanager
def _signature_lock_for(webhook_id: SnowflakeID):
    """Yield the per-webhook mutex guarding key swap + verification."""
    key = int(webhook_id)
    with _SIGNATURE_LOCKS_GUARD:
        entry = _SIGNATURE_LOCKS.get(key)
        if entry is None:
            entry = _WebhookSignatureLock()
            _SIGNATURE_LOCKS[key] = entry
        else:
            _SIGNATURE_LOCKS.move_to_end(key)
        entry.refs += 1
        _prune_signature_locks_locked()
    try:
        with entry.lock:
            yield entry.lock
    finally:
        with _SIGNATURE_LOCKS_GUARD:
            entry.refs -= 1


def _signature_length_is_valid(signature: bytes) -> bool:
    """Constant-time check that ``signature`` is a full Ed25519 signature.

    ``hmac.compare_digest`` compares the encoded lengths in constant time, so
    a caller probing with different signature sizes cannot use timing to learn
    anything about the expected shape of a valid signature.
    """
    return hmac.compare_digest(
        len(signature).to_bytes(4, "big"),
        ED25519_SIGNATURE_LENGTH.to_bytes(4, "big"),
    )


def _is_canonical_timestamp_header(value: str) -> bool:
    """True only for the canonical decimal millisecond form ``sign_payload`` emits.

    ``int()`` additionally accepts leading ``+`` signs, leading zeros,
    underscores, surrounding whitespace and non-ASCII unicode decimal digits.
    Those spellings are never produced by the signer, yet they parse to the
    same integer -- a parser differential that lets one effective timestamp
    have many accepted header spellings. Requiring the exact canonical form
    before parsing keeps the header text and the value bound into the signed
    bytes impossible to diverge (and avoids audit-log confusion).
    """
    if not value or not value.isascii() or not value.isdecimal():
        return False
    # No leading zeros: "0001700..." must not be an alias for "1700...".
    if len(value) > 1 and value[0] == "0":
        return False
    return True


def _replay_cache_key(webhook_id: SnowflakeID, signature: bytes) -> str:
    """Cache-tier key for a seen (webhook_id, signature) pair.

    Keyed on the *decoded* signature bytes rather than the raw header text:
    base64 admits several encodings of the same bytes (URL-safe vs standard
    alphabet, discarded non-alphabet characters, added whitespace), so
    hashing the header verbatim would let a re-encoded replay bypass this
    cache.
    """
    digest = hashlib.sha256(signature).hexdigest()
    return f"webhook:signature-seen:{webhook_id}:{digest}"


def _claim_shared_replay_key(key: str) -> Optional[bool]:
    """Atomically claim ``key`` in the shared cache tier.

    Returns ``True`` when this caller won the claim, ``False`` when another
    process already consumed the pair, and ``None`` when no atomic shared
    backend is available (the caller then falls back to the in-process lock
    plus ``cache_get``/``cache_set`` below).

    A ``cache_get`` miss followed by a later ``cache_set`` is NOT atomic
    across processes: the in-process lock serializes only one worker's
    threads. Two API workers behind a load balancer can both observe the
    miss, both verify the same captured signature, and both accept it within
    the replay window. A conditional ``SET NX`` makes the consume step a
    single-winner operation regardless of how many processes are running,
    which is what replay protection actually requires.
    """
    try:
        from src.core.database.valkey_client import (
            ValkeyOperationError,
            get_client,
            is_available,
        )
    except Exception:  # pragma: no cover - defensive
        return None
    try:
        client = get_client()
        if not client or not is_available():
            return None
        set_nx = getattr(client, "set_nx", None)
        if not callable(set_nx):  # back-compat with clients lacking SET NX
            return None
        return bool(set_nx(key, _REPLAY_MARKER, ttl=_REPLAY_CACHE_TTL_SECONDS))
    except ValkeyOperationError as e:
        logger.warning(f"Webhook replay cache: shared claim failed: {e}")
        return None
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Webhook replay cache: unexpected shared claim error: {e}")
        return None


class SignatureMixin(WebhookManagerTrait):
    """Webhook payload signing with Ed25519.

    Each webhook is provisioned with an Ed25519 keypair at creation time
    (``signing_key_public`` / ``signing_key_private_encrypted``). ``sign_payload``
    attaches a signature + timestamp to outbound deliveries; ``verify_payload``
    validates inbound deliveries and rejects replays outside
    ``WEBHOOK_REPLAY_WINDOW_SECONDS``.
    """

    def sign_payload(
        self, webhook_id: SnowflakeID, payload: Dict[str, Any]
    ) -> Dict[str, str]:
        """Sign a webhook payload using Ed25519.

        Args:
            webhook_id: ID of the webhook
            payload: The payload to sign (will be serialized to JSON)

        Returns:
            Dictionary with signature headers:
            - X-Plexichat-Signature: Ed25519 signature (base64)
            - X-Plexichat-Timestamp: Unix timestamp (milliseconds)
        """
        return self.sign_payload_bytes(webhook_id, canonical_payload_bytes(payload))

    def sign_payload_bytes(
        self, webhook_id: SnowflakeID, payload_bytes: bytes
    ) -> Dict[str, str]:
        """Sign an already-canonicalized payload.

        ``payload_bytes`` MUST be the output of
        :func:`src.core.webhooks.canonical.canonical_payload_bytes`. Callers
        that fan one event out to N endpoints can serialize the shared
        payload once and reuse these bytes for every endpoint instead of
        paying for a full ``json.dumps`` inside :meth:`sign_payload` each time.
        """
        row = self._db.fetch_one(
            "SELECT signing_key_private_encrypted, signing_key_public FROM webhook_webhooks WHERE id = ?",
            (webhook_id,),
        )

        if not row or not dict(row).get("signing_key_private_encrypted"):
            return {}

        row_dict = dict(row)
        try:
            private_key_b64 = decrypt_data(
                row_dict["signing_key_private_encrypted"],
                context=f"webhook:{webhook_id}",
            )
            private_key = base64.b64decode(private_key_b64)
        except Exception as e:
            logger.error(f"Failed to decrypt webhook signing key for {webhook_id}: {e}")
            return {}

        timestamp = int(time.time() * 1000)
        signed_data = (
            f"{SIGNATURE_VERSION}.{timestamp}.".encode("utf-8") + payload_bytes
        )

        from src.utils.encryption import sign_data

        signature = sign_data(signed_data, private_key)

        return {
            SIGNATURE_HEADER: base64.b64encode(signature).decode("utf-8"),
            SIGNATURE_TIMESTAMP_HEADER: str(timestamp),
        }

    def rotate_signing_key(
        self,
        webhook_id: SnowflakeID,
        user_id: Optional[SnowflakeID] = None,
    ) -> bytes:
        """Replace a webhook's Ed25519 signing keypair atomically.

        Rotating a signing secret must not leave a grace window in which
        deliveries signed with the retired key are still accepted. Both the
        public key (used by ``verify_payload``) and the encrypted private key
        (used by ``sign_payload``) are therefore replaced in a *single*
        statement: there is no intermediate database state where one column
        holds the new key and the other still holds the old one. The old key
        is discarded rather than retained as a fallback, so a captured old
        signature fails verification as soon as this method returns.
        """
        row = self._db.fetch_one(
            "SELECT server_id, channel_id FROM webhook_webhooks WHERE id = ?",
            (webhook_id,),
        )
        if not row:
            raise WebhookNotFoundError("Webhook not found")

        if user_id is not None and not self._check_manage_webhooks_permission(
            user_id, row["server_id"], row["channel_id"]
        ):
            raise PermissionDeniedError(
                "Missing permission to manage webhooks", "webhooks.manage"
            )

        private_key, public_key = generate_key_pair()
        private_key_encrypted = encrypt_data(
            base64.b64encode(private_key).decode("utf-8"),
            context=f"webhook:{webhook_id}",
        )
        now = self._get_timestamp()

        # Serialize the key swap with verify_payload()'s fetch + verify. The
        # single atomic UPDATE prevents a mixed-key row, but without this lock
        # a verification that fetched the old key just before the UPDATE could
        # still accept a retired-key signature afterwards. Under the shared
        # lock a delivery observes either the old key in full or the new one.
        with _signature_lock_for(webhook_id):
            self._db.execute(
                """UPDATE webhook_webhooks
                   SET signing_key_public = ?, signing_key_private_encrypted = ?,
                       updated_at = ?
                   WHERE id = ?""",
                (public_key, private_key_encrypted, now, webhook_id),
            )
            # A queued delivery carries the signing headers captured at
            # enqueue time. The delivery worker reuses them verbatim while
            # their timestamp is inside the replay window, so a delivery
            # queued seconds before this rotation would still be POSTed with
            # a signature made by the RETIRED key -- a grace window the swap
            # above is supposed to forbid. Drop the captured headers on every
            # still-claimable row so delivery re-signs with the NEW key
            # (WebhookDeliverer._effective_signing_headers re-signs whenever
            # the stored timestamp is missing or stale).
            #
            # 'delivering' rows must be included too. Clearing their metadata
            # cannot recall a POST a live worker already started -- that is
            # unavoidable -- but a row whose worker died is re-claimed to
            # 'pending' by WebhookRetryQueue.cleanup_expired(). If its headers
            # survived the rotation, that later attempt would be signed with
            # the retired key, re-opening the grace window indefinitely. Drop
            # the headers here so any re-claim re-signs with the NEW key.
            self._db.execute(
                """UPDATE webhook_retry_queue
                   SET metadata = NULL
                   WHERE webhook_id = ? AND status IN ('pending', 'retrying', 'delivering')""",
                (webhook_id,),
            )
        logger.info(f"Rotated signing key for webhook {webhook_id}")
        return public_key

    def verify_payload(
        self,
        webhook_id: SnowflakeID,
        payload: Dict[str, Any],
        signature_header: Optional[str],
        timestamp_header: Optional[str],
        now_ms: Optional[int] = None,
    ) -> bool:
        """Verify an inbound webhook payload signature.

        Returns True only when the signature matches AND the
        timestamp is within ``WEBHOOK_REPLAY_WINDOW_SECONDS`` of
        ``now_ms`` (caller-provided UTC ms; defaults to
        ``int(time.time() * 1000)``).

        Args:
            webhook_id: Webhook whose signing public key will be used.
            payload: Decoded payload dict (must match the bytes that
                were signed).
            signature_header: ``X-Plexichat-Signature`` header value
                (base64 URL-safe or standard).
            timestamp_header: ``X-Plexichat-Timestamp`` header value
                (string milliseconds).
            now_ms: Optional caller-supplied current time in ms.

        Returns:
            True if the signature is valid AND not replayed.
        """
        # Bound untrusted header values BEFORE decoding/verification so a
        # hostile caller cannot make the verifier churn on megabyte inputs.
        if (
            not signature_header
            or not timestamp_header
            or len(signature_header) > MAX_SIGNATURE_HEADER_LENGTH
            or len(timestamp_header) > MAX_TIMESTAMP_HEADER_LENGTH
        ):
            return False

        # Reject non-canonical spellings BEFORE int() parses them, so the
        # timestamp bound into the signed bytes is exactly what the header
        # says (no '+', leading zeros, whitespace or unicode digit aliases).
        if not isinstance(timestamp_header, str) or not _is_canonical_timestamp_header(
            timestamp_header
        ):
            return False
        timestamp = int(timestamp_header)

        current = int(now_ms if now_ms is not None else time.time() * 1000)
        skew_ms = abs(current - timestamp)
        if skew_ms > WEBHOOK_REPLAY_WINDOW_SECONDS * 1000:
            logger.warning(
                f"Webhook signature rejected (timestamp outside "
                f"{WEBHOOK_REPLAY_WINDOW_SECONDS}s window): "
                f"webhook={webhook_id}, skew_ms={skew_ms}"
            )
            return False

        # Decode the signature (URL-safe or standard base64).
        try:
            try:
                signature = base64.urlsafe_b64decode(signature_header.encode("ascii"))
            except Exception:
                signature = base64.b64decode(signature_header.encode("ascii"))
        except Exception as e:
            logger.warning(
                f"Webhook signature header was not valid base64 for {webhook_id}: {e}"
            )
            return False

        # A well-formed Ed25519 signature is exactly 64 bytes. Reject any
        # other size in constant time before the public-key verification.
        if not _signature_length_is_valid(signature):
            logger.warning(
                f"Webhook signature had invalid length for {webhook_id}: "
                f"{len(signature)} bytes (expected {ED25519_SIGNATURE_LENGTH})"
            )
            return False

        # Replay protection: a valid (webhook_id, signature) pair seen
        # inside the ±window must never be accepted twice. The cache tier
        # (Valkey with in-memory fallback) records seen pairs with a TTL
        # covering the whole window. The key is derived from the DECODED
        # signature bytes so alternative base64 encodings of the same
        # signature share one cache entry.
        from src.core.database.cache import cache_get, cache_set

        replay_key = _replay_cache_key(webhook_id, signature)
        payload_bytes = canonical_payload_bytes(payload)
        signed_data = (
            f"{SIGNATURE_VERSION}.{timestamp}.".encode("utf-8") + payload_bytes
        )

        # The key fetch, replay lookup, signature verification and "consumed"
        # write must be one atomic step: otherwise two concurrent deliveries of
        # one captured signature can both see a miss and both be accepted, and
        # a rotation committing mid-verification could swap in a new key after
        # the old one was fetched. Holding the lock for all of them makes a
        # delivery observe a single consistent key + replay state.
        with _signature_lock_for(webhook_id):
            row = self._db.fetch_one(
                "SELECT signing_key_public FROM webhook_webhooks WHERE id = ?",
                (webhook_id,),
            )
            if not row:
                return False

            public_key_bytes = dict(row).get("signing_key_public")
            if not public_key_bytes:
                return False

            # The schema stores the raw 32-byte public key (BLOB/BYTEA). Some
            # rows may carry a base64 text form instead; normalize both so a
            # key written by any version verifies.
            if isinstance(public_key_bytes, str):
                try:
                    public_key_bytes = base64.b64decode(
                        public_key_bytes.encode("ascii")
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to decode signing-key for inbound webhook "
                        f"{webhook_id}: {e}"
                    )
                    return False
            # Enforce the 32-byte Ed25519 public-key length for *both* storage
            # forms (raw BLOB and base64 TEXT). The TEXT branch previously
            # skipped this check and leaned on the crypto library to reject
            # malformed keys; failing here keeps verification fail-closed when
            # a key is rotated between the BLOB and TEXT encodings.
            if len(public_key_bytes) != 32:
                logger.error(
                    f"Signing public key for webhook {webhook_id} is not 32 bytes"
                )
                return False

            try:
                consumed = cache_get(replay_key)
                if consumed is not None:
                    # Validate the stored marker in constant time. Any unexpected
                    # value is logged but STILL treated as a replay: a corrupted
                    # cache entry must never downgrade to "fresh" and let the
                    # signature through a second time.
                    if not hmac.compare_digest(str(consumed), _REPLAY_MARKER):
                        logger.error(
                            f"Unexpected webhook replay-cache marker for {webhook_id}"
                        )
                    logger.warning(
                        f"Webhook signature rejected as REPLAY for {webhook_id} "
                        f"(signature/timestamp pair already used within the "
                        f"{WEBHOOK_REPLAY_WINDOW_SECONDS}s window)"
                    )
                    return False
            except Exception as e:  # cache failure must not bypass the check silently
                logger.error(
                    f"Replay-cache lookup failed for webhook {webhook_id}: {e}"
                )
                return False

            try:
                Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
                    signature, signed_data
                )
            except InvalidSignature:
                logger.warning(f"Webhook signature mismatch for {webhook_id}")
                return False
            except Exception as e:
                logger.error(
                    f"Webhook signature verification error for {webhook_id}: {e}"
                )
                return False

            # Consume the pair atomically before reporting success. With a
            # shared cache tier, SET NX is the single-winner decision: a
            # racing worker in ANOTHER process loses the claim and is rejected
            # even though its Ed25519 verification succeeded. Without a shared
            # backend, fall back to the in-process guarded cache below.
            claimed = _claim_shared_replay_key(replay_key)
            if claimed is True:
                return True
            if claimed is False:
                logger.warning(
                    f"Webhook signature rejected as REPLAY for {webhook_id} "
                    "(signature already consumed by another worker)"
                )
                return False
            try:
                cache_set(replay_key, _REPLAY_MARKER, ttl=_REPLAY_CACHE_TTL_SECONDS)
            except Exception as e:
                logger.error(f"Replay-cache store failed for webhook {webhook_id}: {e}")
                return False
            return True
