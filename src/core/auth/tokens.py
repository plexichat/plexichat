"""
API access tokens module.

This module handles API access token CRUD operations, verification, scopes, and usage tracking.
"""

import base64
import hashlib
import hmac
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .models import AccessToken
from ._lazy import _get_auth_manager


# ---- Token format / expiry bounds -----------------------------------------
# Every credential minted here is ``[prefix.]<id>.<base64url secret>``. The
# limits below turn parse_token into a strict grammar rather than a generic
# string splitter: oversized bodies are rejected before any hashing/DB work
# (DoS bound), ids must be canonical positive integers, and secrets must be
# base64url with the entropy our generators actually use.
MAX_TOKEN_LENGTH = 512
MAX_TOKEN_ID = 2**63 - 1
MAX_TOKEN_ID_DIGITS = 19
# Secrets are minted with >=32 bytes of entropy; the parse-time floor is
# intentionally lower so credentials issued by older builds (and seeded
# fixtures) remain verifiable. Entropy is guaranteed at generation, not here.
MIN_TOKEN_SECRET_LENGTH = 8
MAX_TOKEN_SECRET_LENGTH = 128
_TOKEN_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOKEN_ID_RE = re.compile(r"^[0-9]{1,19}$")
# Upper bound on an expiry expressed in epoch milliseconds (year 2100).
MAX_EXPIRY_MS = 4_102_444_800_000
_EXPIRY_RE = re.compile(r"^[0-9]{1,16}$")


def generate_token_secret(length: int = 32) -> str:
    """Generate a cryptographically secure random token secret."""
    random_bytes = os.urandom(length)
    return base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")


def hash_token(token_secret: str) -> str:
    """Hash a token secret for storage using SHA-256."""
    return hashlib.sha256(token_secret.encode("utf-8")).hexdigest()


def create_session_token(session_id: int, secret_length: int = 32) -> Tuple[str, str]:
    """Create a new session token."""
    secret = generate_token_secret(secret_length)
    full_token = f"{session_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_bot_token(bot_id: int, secret_length: int = 48) -> Tuple[str, str]:
    """Create a new bot token."""
    secret = generate_token_secret(secret_length)
    full_token = f"bot.{bot_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_email_token(token_id: int, secret_length: int = 32) -> Tuple[str, str]:
    """Create a token for email verification or password reset."""
    secret = generate_token_secret(secret_length)
    full_token = f"email.{token_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_2fa_challenge_token(
    challenge_id: int, secret_length: int = 32
) -> Tuple[str, str]:
    """Create a temporary token for 2FA challenge."""
    secret = generate_token_secret(secret_length)
    full_token = f"2fa.{challenge_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_ws_ticket(ticket_id: int, secret_length: int = 32) -> Tuple[str, str]:
    """Create a short-lived single-use gateway (WebSocket) ticket."""
    secret = generate_token_secret(secret_length)
    full_token = f"wst.{ticket_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def _parse_token_id(raw: Any) -> Optional[int]:
    """Parse a canonical, bounded positive token id.

    ``int()`` alone accepts signs, whitespace, underscores and arbitrarily
    long digit strings. Those forms are never minted and only widen the
    confusion/DoS surface, so require the exact decimal representation.
    """
    if not isinstance(raw, str) or not _TOKEN_ID_RE.fullmatch(raw):
        return None
    # Reject leading zeros (including "0"): minted ids start at 1.
    if raw[0] == "0" or len(raw) > MAX_TOKEN_ID_DIGITS:
        return None
    value = int(raw)
    if value < 1 or value > MAX_TOKEN_ID:
        return None
    return value


def _valid_token_secret(raw: Any) -> bool:
    """Whether ``raw`` is a plausible base64url token secret."""
    return (
        isinstance(raw, str)
        and MIN_TOKEN_SECRET_LENGTH <= len(raw) <= MAX_TOKEN_SECRET_LENGTH
        and bool(_TOKEN_SECRET_RE.fullmatch(raw))
    )


def parse_token(token: str) -> Optional[dict]:
    """Parse a token into its components, strictly.

    Returns ``None`` for anything that is not a bounded, well-formed token:
    non-strings, oversized bodies, non-canonical ids, short/non-base64url
    secrets and unknown prefixes. Callers translate ``None`` into an
    authentication denial; no partial/ambiguous token is ever produced.
    """
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    # Any control character / whitespace is outside the minted grammar.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in token):
        return None

    parts = token.split(".")

    prefixed = {"bot", "email", "2fa", "wst"}
    if len(parts) == 3 and parts[0] in prefixed:
        token_id = _parse_token_id(parts[1])
        if token_id is None or not _valid_token_secret(parts[2]):
            return None
        return {"token_type": parts[0], "id": token_id, "secret": parts[2]}

    if len(parts) == 2:
        session_id = _parse_token_id(parts[0])
        if session_id is None or not _valid_token_secret(parts[1]):
            return None
        return {"token_type": "session", "id": session_id, "secret": parts[1]}

    return None


def parse_expiry_ms(value: Any) -> Optional[int]:
    """Strictly parse an epoch-millisecond expiry.

    Fails closed (``None``) for booleans, floats, negatives, malformed
    numeric strings, zero and out-of-range values. Stored expiries come from
    our own writes, but a corrupted/partially migrated row must deny access
    rather than compare as "not expired" and grant a permanent session.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and _EXPIRY_RE.fullmatch(value):
        parsed = int(value)
    else:
        return None
    if parsed < 1 or parsed > MAX_EXPIRY_MS:
        return None
    return parsed


def is_expired(expires_at: Any, now_ms: Optional[int] = None) -> bool:
    """Whether an expiry is expired or unparseable (fail closed).

    An invalid clock value must never turn this check into a grant: callers
    pass ``now_ms`` from their own clock, and a corrupt/zero/units-mismatched
    value (e.g. seconds instead of milliseconds) would otherwise compare as
    "not expired" and hand out a permanent session. Any clock value that is
    not a plausible epoch-millisecond integer is treated as expiry.
    """
    parsed = parse_expiry_ms(expires_at)
    if parsed is None:
        return True
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    elif (
        isinstance(now_ms, bool)
        or not isinstance(now_ms, int)
        or now_ms < 1
        or now_ms > MAX_EXPIRY_MS
    ):
        return True
    return parsed <= now_ms


def is_malformed_token_input(token: Any) -> bool:
    """Whether a value can even be considered a token credential.

    Used before hashing/DB work so a missing, non-string or oversized auth
    context is denied cleanly instead of raising deep in a cache key builder
    (which previously turned a bad header into a 500 instead of a 401).
    """
    return not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH


def safe_token_cache_key(token: Any) -> str:
    """Build a verification cache key without dereferencing untrusted input."""
    if is_malformed_token_input(token):
        return "token_verify:invalid"
    return f"token_verify:str:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def safe_token_binding_key(ip_address: Optional[str], user_agent: Optional[str]) -> str:
    """Return a bounded, hash-only suffix scoping a verify-cache key to IP/UA.

    ``verify_token`` enforces IP/User-Agent binding against the session row when
    ``security.token_binding`` is enabled. A cached ``TokenInfo`` must never be
    replayed from a *different* network context -- otherwise a stolen bearer
    token could ride the legitimate context's cached result past the binding
    check. Mixing a digest of the presented IP and user agent into the cache key
    makes every binding context its own entry while keeping raw IPs and user
    agents out of the keyspace (and the key length fixed). Absent context yields
    an empty suffix, which is safe: when binding is off the verifier does not
    depend on these values at all, and when binding is on a missing IP/UA is
    denied inside the verifier itself.
    """
    if not ip_address and not user_agent:
        return ""
    material = f"{ip_address or ''}\x00{user_agent or ''}"
    return ":b:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def verify_token_hash(token_secret: str, stored_hash: str) -> bool:
    """Verify a token secret against its stored hash (fail closed).

    Both operands are validated before any hashing/comparison so a malformed
    credential or a corrupt stored hash is a clean denial rather than an
    ``AttributeError`` raised out of ``hash_token`` (which turned a bad auth
    value into a 500 and bypassed the constant-time comparator entirely).
    """
    if not isinstance(token_secret, str) or not isinstance(stored_hash, str):
        return False
    if not token_secret or len(token_secret) > MAX_TOKEN_LENGTH:
        return False
    computed_hash = hash_token(token_secret)
    return _constant_time_compare(computed_hash, stored_hash)


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def create_api_access_token(
    name: Optional[str],
    created_by: Optional[int],
    token_value: Optional[str] = None,
    description: Optional[str] = None,
    expires_at: Optional[int] = None,
    scope_mode: str = "none",
) -> AccessToken:
    return (
        _get_auth_manager()
        .get_instance()
        .create_api_access_token(
            name,
            created_by,
            token_value,
            description=description,
            expires_at=expires_at,
            scope_mode=scope_mode,
        )
    )


def list_api_access_tokens(
    include_revoked: bool = True, limit: int = 200, offset: int = 0
) -> List[AccessToken]:
    return (
        _get_auth_manager()
        .get_instance()
        .list_api_access_tokens(include_revoked, limit, offset)
    )


def get_api_access_token(token_id: int) -> Optional[AccessToken]:
    return _get_auth_manager().get_instance().get_api_access_token(token_id)


def update_api_access_token(
    token_id: int,
    updated_by: Optional[int],
    name: Optional[str] = None,
    description: Optional[str] = None,
    expires_at: Optional[int] = None,
    clear_expiry: bool = False,
    scope_mode: Optional[str] = None,
) -> Optional[AccessToken]:
    return (
        _get_auth_manager()
        .get_instance()
        .update_api_access_token(
            token_id,
            updated_by,
            name=name,
            description=description,
            expires_at=expires_at,
            clear_expiry=clear_expiry,
            scope_mode=scope_mode,
        )
    )


def revoke_api_access_token(token_id: int, revoked_by: Optional[int]) -> bool:
    return (
        _get_auth_manager().get_instance().revoke_api_access_token(token_id, revoked_by)
    )


def unrevoke_api_access_token(token_id: int, unrevoked_by: Optional[int]) -> bool:
    return (
        _get_auth_manager()
        .get_instance()
        .unrevoke_api_access_token(token_id, unrevoked_by)
    )


def rotate_api_access_token(
    token_id: int,
    rotated_by: Optional[int],
    token_value: Optional[str] = None,
) -> Optional[AccessToken]:
    return (
        _get_auth_manager()
        .get_instance()
        .rotate_api_access_token(token_id, rotated_by, token_value)
    )


def add_api_access_token_scope(
    token_id: int,
    scope_type: str,
    value: str,
    created_by: Optional[int],
) -> Dict[str, Any]:
    return (
        _get_auth_manager()
        .get_instance()
        .add_api_access_token_scope(token_id, scope_type, value, created_by)
    )


def remove_api_access_token_scope(token_id: int, scope_id: int) -> bool:
    return (
        _get_auth_manager()
        .get_instance()
        .remove_api_access_token_scope(token_id, scope_id)
    )


def list_api_access_token_scopes(token_id: int) -> List[Dict[str, Any]]:
    return _get_auth_manager().get_instance().list_api_access_token_scopes(token_id)


def get_api_access_token_usage(
    token_id: int,
    recent_limit: int = 100,
) -> Dict[str, Any]:
    return (
        _get_auth_manager()
        .get_instance()
        .get_api_access_token_usage(token_id, recent_limit)
    )


def verify_api_access_token(
    token: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
) -> bool:
    return (
        _get_auth_manager()
        .get_instance()
        .verify_api_access_token(
            token,
            ip_address=ip_address,
            user_agent=user_agent,
            path=path,
            method=method,
        )
    )


def is_api_access_token_required() -> bool:
    return _get_auth_manager().get_instance().is_api_access_token_required()


__all__ = [
    "generate_token_secret",
    "hash_token",
    "create_session_token",
    "create_bot_token",
    "create_email_token",
    "create_2fa_challenge_token",
    "create_ws_ticket",
    "parse_token",
    "parse_expiry_ms",
    "is_expired",
    "is_malformed_token_input",
    "safe_token_cache_key",
    "verify_token_hash",
    "create_api_access_token",
    "list_api_access_tokens",
    "get_api_access_token",
    "update_api_access_token",
    "revoke_api_access_token",
    "unrevoke_api_access_token",
    "rotate_api_access_token",
    "add_api_access_token_scope",
    "remove_api_access_token_scope",
    "list_api_access_token_scopes",
    "get_api_access_token_usage",
    "verify_api_access_token",
    "is_api_access_token_required",
]
