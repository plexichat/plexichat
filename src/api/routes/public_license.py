"""
Public license endpoints - challenge-response license export.

These endpoints allow a holder of the Plexichat licensing private key
(the "signing authority") to remotely verify and download the license
of a running Plexichat instance. They are intentionally unauthenticated
because the cryptographic signature *is* the authentication.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, status

import utils.logger as logger

router = APIRouter(prefix="/public/license", tags=["Public License"])

_MAX_NONCE_LEN = 128
_MAX_SIGNATURE_LEN = 512
_DEFAULT_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Nonce store
# ---------------------------------------------------------------------------

# Lua script: atomically GET-and-DELETE a key. Returns 1 if the key
# existed (and was therefore consumed), 0 otherwise.
_CONSUME_LUA = """
local v = redis.call('GET', KEYS[1])
if v == false then
    return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


class NonceStore:
    """Thread-safe single-use nonce store with a TTL.

    Nonces are opaque random byte strings. The store keeps each nonce
    mapped to its absolute expiry timestamp; lookups return ``False``
    for unknown, expired, or already-consumed nonces.

    Backed by Redis when available so the store works across multiple
    API workers. Falls back to an in-process dict when Redis is not
    configured (single-worker mode only).
    """

    NONCE_KEY_PREFIX = "lic_nonce:"

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._nonces: Dict[str, float] = {}
        self._redis = None
        self._redis_checked = False

    def _get_redis(self):
        """Return the configured Redis client, or ``None`` if unavailable.

        Result is memoized after the first call.
        """
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            from src.core.database.redis_client import get_client

            self._redis = get_client()
        except Exception as e:
            logger.debug(f"Redis client unavailable for nonce store: {e}")
            self._redis = None
        return self._redis

    def _redis_key(self, nonce: str) -> str:
        return f"{self.NONCE_KEY_PREFIX}{nonce}"

    def issue(self) -> Tuple[str, int]:
        """Generate a new nonce, store it, and return ``(nonce, ttl)``."""
        raw = secrets.token_bytes(32)
        nonce = base64.b64encode(raw).decode("ascii")
        redis_client = self._get_redis()
        if redis_client is not None:
            # Fail closed on Redis errors: do NOT fall through to the
            # in-process store. A split-brain nonce would let an
            # attacker replay a nonce via a different worker.
            try:
                redis_client.set(self._redis_key(nonce), "1", ttl=self._ttl_seconds)
                return nonce, self._ttl_seconds
            except Exception as e:
                logger.error(f"Redis SET failed for license nonce: {e}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="License challenge store is unavailable",
                )
        expires_at = time.time() + self._ttl_seconds
        with self._lock:
            self._purge_expired_locked()
            self._nonces[nonce] = expires_at
        return nonce, self._ttl_seconds

    def consume(self, nonce: str) -> bool:
        """Atomically validate-and-remove a nonce. Returns True on success."""
        if not nonce:
            return False
        redis_client = self._get_redis()
        if redis_client is not None:
            try:
                result = redis_client.eval_lua(
                    _CONSUME_LUA, keys=[self._redis_key(nonce)], args=[]
                )
                return bool(int(result))
            except Exception as e:
                logger.error(f"Redis EVAL failed for license nonce: {e}")
                return False
        with self._lock:
            entry = self._nonces.pop(nonce, None)
            if entry is None:
                return False
            if entry < time.time():
                return False
            return True

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired = [k for k, v in self._nonces.items() if v < now]
        for k in expired:
            del self._nonces[k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._nonces)


# Module-level singleton.
nonce_store = NonceStore(ttl_seconds=_DEFAULT_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Public key loading
# ---------------------------------------------------------------------------


def _get_public_key_bytes() -> Optional[bytes]:
    """Return the license verification public key as raw bytes."""
    try:
        from src.utils.common_utils.utils.licensing.core import LicenseManager

        key_b64 = getattr(LicenseManager, "_PUBLIC_KEY_BASE64", None)
        if not key_b64:
            logger.error(
                "LicenseManager has no public key configured; "
                "did licensing.setup() run at startup?"
            )
            return None
        try:
            raw = base64.b64decode(key_b64, validate=True)
        except Exception as e:
            logger.error(f"Configured license public key is not valid base64: {e}")
            return None
        if len(raw) != 32:
            logger.error(
                f"Configured license public key is {len(raw)} bytes, expected 32"
            )
            return None
        return raw
    except Exception as e:
        logger.error(f"Failed to load license public key: {e}")
        return None


def _fingerprint() -> Optional[str]:
    """Return a short SHA-256 fingerprint of the public key (first 16 hex chars)."""
    raw = _get_public_key_bytes()
    if raw is None:
        return None
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/challenge",
    summary="Issue a single-use license challenge nonce",
    responses={
        200: {
            "description": "A fresh nonce and its TTL.",
        },
        503: {"description": "License challenge store is unavailable."},
    },
)
async def get_license_challenge() -> dict:
    """Return a single-use challenge nonce for the export endpoint.

    The caller is expected to sign this nonce with the Plexichat
    license private key and present the signature to
    ``/public/license/export``.
    """
    nonce, ttl = nonce_store.issue()
    return {"nonce": nonce, "ttl_seconds": ttl}


@router.get(
    "/export",
    summary="Export the active license after verifying a signed challenge",
    responses={
        200: {"description": "The full license document."},
        400: {"description": "Malformed nonce or signature."},
        401: {"description": "Missing, unknown, expired, or already-used nonce."},
        403: {"description": "Signature did not verify against the public key."},
        404: {"description": "No license is loaded on this instance (free tier)."},
        503: {"description": "License public key is not configured on this instance."},
    },
)
async def export_license(
    nonce: str = Query(
        ..., min_length=1, max_length=_MAX_NONCE_LEN, description="Challenge nonce"
    ),
    signature: str = Query(
        ...,
        min_length=1,
        max_length=_MAX_SIGNATURE_LEN,
        description="Base64 Ed25519 signature of the nonce",
    ),
) -> dict:
    """Verify the signed challenge and return the active license.

    The signature is verified against the same Ed25519 public key that
    is used to verify license signatures. Possession of the matching
    private key is the sole authentication requirement.

    Order of operations is *verify first, consume last* so that an
    attacker who can observe a freshly-issued nonce cannot burn it
    by submitting a garbage signature. The legitimate signing
    authority can simply retry with the original signature.
    """
    try:
        sig_bytes = base64.b64decode(signature, validate=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature is not valid base64",
        )
    if len(sig_bytes) != 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature must decode to exactly 64 bytes",
        )

    public_key_bytes = _get_public_key_bytes()
    if public_key_bytes is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="License public key is not configured on this instance",
        )

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        try:
            public_key.verify(sig_bytes, nonce.encode("utf-8"))
        except InvalidSignature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Signature did not verify against the Plexichat public key",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Signature verification failed",
        )

    if not nonce_store.consume(nonce):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or already-used nonce",
        )

    try:
        from src.utils import licensing as licensing_module

        if licensing_module.is_free_tier():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No license is loaded on this instance (free tier mode)",
            )

        license_dict = licensing_module.to_dict()
        validation = licensing_module.get_validation_result()
        validation_dict = None
        if validation:
            validation_dict = {
                "is_valid": getattr(validation, "is_valid", None),
                "is_expired": getattr(validation, "is_expired", None),
                "is_signature_valid": getattr(validation, "is_signature_valid", None),
                "instance_id": getattr(validation, "instance_id", None),
                "error_message": getattr(validation, "error_message", None),
            }
        return {
            "license": license_dict,
            "validation": validation_dict,
            "instance_id": licensing_module.get_instance_id(),
            "expires_at": licensing_module.get_expiry_timestamp(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export license: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to read license from server",
        )


@router.get(
    "/info",
    summary="Public info about the licensing system (no secrets)",
    responses={200: {"description": "Boolean describing the licensing system state."}},
)
async def license_info() -> dict:
    """Lightweight, unauthenticated info about the licensing subsystem.

    Returns only booleans and counts; no key material, no license body.
    Useful as a reachability check.
    """
    try:
        from src.utils import licensing as licensing_module

        return {
            "available": True,
            "free_tier": licensing_module.is_free_tier(),
            "valid": licensing_module.is_valid(),
            "has_instance_id": licensing_module.get_instance_id() is not None,
            "public_key_fingerprint": _fingerprint(),
        }
    except Exception as e:
        logger.debug(f"license_info error: {e}")
        return {"available": False}


__all__ = ["router", "NonceStore", "nonce_store"]
