"""
OAuth2 tokens - Token generation and verification.
"""

import re
import base64
import secrets
import hashlib
from typing import Any, Tuple, Optional

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

MAX_TOKEN_LENGTH = 512
MAX_SECRET_LENGTH = 256
MIN_SECRET_LENGTH = 16
SECRET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TOKEN_TYPES = frozenset({"auth", "access", "refresh", "int"})

# RFC 7636 PKCE grammar. The verifier is a 43-128 character unreserved string;
# an S256 challenge is BASE64URL(SHA256(verifier)) and is therefore exactly 43
# unreserved characters. Only the S256 method is accepted — ``plain`` is not.
PKCE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
PKCE_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9\-._~]{43}$")
PKCE_METHOD_S256 = "S256"


def _bounded_length(length: int, field: str) -> int:
    """Keep generator entropy configurable but never absurd."""
    return bounded_int(
        length, field, minimum=MIN_SECRET_LENGTH, maximum=MAX_SECRET_LENGTH // 2
    )


def validate_client_secret(value: Any, *, field: str = "client_secret") -> str:
    """Validate a presented client secret before it reaches a hash comparison."""
    text = reject_control_characters(value, field).strip()
    if not SECRET_PATTERN.fullmatch(text):
        raise HardeningViolation(f"{field} has an invalid format or length")
    return text


def validate_token_string(value: Any, *, field: str = "token") -> str:
    """Bound an opaque token string before parsing or hashing it."""
    text = reject_control_characters(value, field).strip()
    if not text or len(text) > MAX_TOKEN_LENGTH:
        raise HardeningViolation(f"{field} has an invalid length")
    return text


def validate_token_hash(value: Any, *, field: str = "token_hash") -> str:
    """Require a SHA-256 hex digest in the stored hash fields."""
    text = str(value or "")
    if not HASH_PATTERN.fullmatch(text):
        raise HardeningViolation(f"{field} is not a SHA-256 hex digest")
    return text


def generate_authorization_code(code_id: int, length: int = 32) -> Tuple[str, str]:
    """
    Generate an authorization code.

    Args:
        code_id: ID of the authorization code record
        length: Length of random bytes

    Returns:
        Tuple of (full_code, code_hash)
    """
    random_bytes = secrets.token_urlsafe(_bounded_length(length, "length"))
    full_code = f"auth.{code_id}.{random_bytes}"
    code_hash = hash_token(random_bytes)
    return full_code, code_hash


def generate_access_token(token_id: int, length: int = 32) -> Tuple[str, str]:
    """
    Generate an access token.

    Args:
        token_id: ID of the token record
        length: Length of random bytes

    Returns:
        Tuple of (full_token, token_hash)
    """
    random_bytes = secrets.token_urlsafe(_bounded_length(length, "length"))
    full_token = f"access.{token_id}.{random_bytes}"
    token_hash = hash_token(random_bytes)
    return full_token, token_hash


def generate_refresh_token(token_id: int, length: int = 48) -> Tuple[str, str]:
    """
    Generate a refresh token.

    Args:
        token_id: ID of the token record
        length: Length of random bytes

    Returns:
        Tuple of (full_token, token_hash)
    """
    random_bytes = secrets.token_urlsafe(_bounded_length(length, "length"))
    full_token = f"refresh.{token_id}.{random_bytes}"
    token_hash = hash_token(random_bytes)
    return full_token, token_hash


def generate_interaction_token(
    interaction_id: int, length: int = 32
) -> Tuple[str, str]:
    """
    Generate an interaction token.

    Args:
        interaction_id: ID of the interaction record
        length: Length of random bytes

    Returns:
        Tuple of (full_token, token_hash)
    """
    random_bytes = secrets.token_urlsafe(_bounded_length(length, "length"))
    full_token = f"int.{interaction_id}.{random_bytes}"
    token_hash = hash_token(random_bytes)
    return full_token, token_hash


def generate_client_secret(length: int = 32) -> Tuple[str, str]:
    """
    Generate a client secret.

    Args:
        length: Length of random bytes

    Returns:
        Tuple of (secret, secret_hash)
    """
    secret = secrets.token_urlsafe(_bounded_length(length, "length"))
    secret_hash = hash_token(secret)
    return secret, secret_hash


def hash_token(token: str) -> str:
    """
    Hash a token using SHA-256.

    Args:
        token: Token to hash

    Returns:
        Hex-encoded hash
    """
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token_hash(token: str, expected_hash: str) -> bool:
    """
    Verify a token against its hash using constant-time comparison.

    Args:
        token: Token to verify
        expected_hash: Expected hash

    Returns:
        True if token matches hash
    """
    try:
        candidate = validate_token_string(token)
        expected = validate_token_hash(expected_hash)
    except HardeningViolation:
        return False
    actual_hash = hash_token(candidate)
    return secrets.compare_digest(actual_hash, expected)


def validate_pkce_verifier(value: Any, *, field: str = "code_verifier") -> str:
    """Return a bounded RFC 7636 verifier or raise :class:`HardeningViolation`."""
    text = reject_control_characters(value, field).strip()
    if not PKCE_VERIFIER_PATTERN.fullmatch(text):
        raise HardeningViolation(
            f"{field} must be 43-128 unreserved RFC 7636 characters"
        )
    return text


def validate_pkce_challenge(value: Any, *, field: str = "code_challenge") -> str:
    """Return a canonical S256 challenge (43 unreserved chars) or raise."""
    text = reject_control_characters(value, field).strip()
    if not PKCE_CHALLENGE_PATTERN.fullmatch(text):
        raise HardeningViolation(f"{field} must be a 43-character S256 challenge")
    return text


def validate_pkce_method(value: Any, *, field: str = "code_challenge_method") -> str:
    """Accept only the S256 challenge method (``plain`` is rejected)."""
    if value is None:
        return PKCE_METHOD_S256
    text = reject_control_characters(value, field).strip()
    if text != PKCE_METHOD_S256:
        raise HardeningViolation("only the S256 code_challenge_method is supported")
    return text


def create_s256_challenge(code_verifier: str) -> str:
    """Derive ``BASE64URL(SHA256(ASCII(verifier)))`` without padding."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_pkce_challenge(code_verifier: Any, stored_challenge: Any) -> bool:
    """Constant-time verification of a verifier against a stored S256 challenge.

    The verifier and stored challenge are both bounded before hashing, and the
    derived digest is compared with ``secrets.compare_digest`` so a wrong
    verifier cannot be distinguished from a correct one by response timing.
    Malformed inputs fail closed rather than raising.
    """
    try:
        candidate = validate_pkce_verifier(code_verifier)
        expected = validate_pkce_challenge(stored_challenge)
    except HardeningViolation:
        return False
    return secrets.compare_digest(create_s256_challenge(candidate), expected)


def parse_oauth_token(token: str) -> Optional[dict]:
    """
    Parse an OAuth token string.

    Args:
        token: Full token string

    Returns:
        Dict with token_type, id, and secret, or None if invalid
    """
    try:
        candidate = validate_token_string(token)
    except HardeningViolation:
        return None

    parts = candidate.split(".", 2)
    if len(parts) != 3:
        return None

    token_type, token_id, secret = parts

    if token_type not in TOKEN_TYPES or not SECRET_PATTERN.fullmatch(secret):
        return None

    try:
        parsed_id = parse_id(token_id, "token_id")
    except HardeningViolation:
        return None

    return {
        "token_type": token_type,
        "id": parsed_id,
        "secret": secret,
    }
