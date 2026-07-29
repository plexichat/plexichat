"""
API access tokens module.

This module handles API access token CRUD operations, verification, scopes, and usage tracking.
"""

import base64
import hashlib
import os
from typing import Any, Dict, List, Optional, Tuple

from .models import AccessToken
from ._lazy import _get_auth_manager


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


def parse_token(token: str) -> Optional[dict]:
    """Parse a token into its components."""
    if not token or not isinstance(token, str):
        return None

    parts = token.split(".")

    if len(parts) == 3 and parts[0] == "bot":
        try:
            bot_id = int(parts[1])
            return {"token_type": "bot", "id": bot_id, "secret": parts[2]}
        except ValueError:
            return None

    if len(parts) == 3 and parts[0] == "email":
        try:
            token_id = int(parts[1])
            return {"token_type": "email", "id": token_id, "secret": parts[2]}
        except ValueError:
            return None

    if len(parts) == 3 and parts[0] == "2fa":
        try:
            challenge_id = int(parts[1])
            return {"token_type": "2fa", "id": challenge_id, "secret": parts[2]}
        except ValueError:
            return None

    if len(parts) == 2:
        try:
            session_id = int(parts[0])
            return {"token_type": "session", "id": session_id, "secret": parts[1]}
        except ValueError:
            return None

    return None


def verify_token_hash(token_secret: str, stored_hash: str) -> bool:
    """Verify a token secret against its stored hash."""
    computed_hash = hash_token(token_secret)
    return _constant_time_compare(computed_hash, stored_hash)


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


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


def list_api_access_tokens(include_revoked: bool = True) -> List[AccessToken]:
    return _get_auth_manager().get_instance().list_api_access_tokens(include_revoked)


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
    "parse_token",
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
