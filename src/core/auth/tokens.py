"""
Secure token generation and validation.

Token format:
- User sessions: <session_id>.<random_secret>
- Bot tokens: bot.<bot_id>.<random_secret>

Only the hash of the secret is stored in the database.
"""

import os
import base64
import hashlib
from typing import Optional, Tuple, Dict, Any


def generate_token_secret(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token secret.

    Args:
        length: Number of random bytes (default 32)

    Returns:
        Base64-encoded random string (URL-safe)
    """
    random_bytes = os.urandom(length)
    return base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")


def hash_token(token_secret: str) -> str:
    """
    Hash a token secret for storage.

    Uses SHA-256 for deterministic, fixed-length hashes.

    Args:
        token_secret: The secret portion of the token

    Returns:
        Argon2id hash string
    """
    if not token_secret:
        raise ValueError("Empty token secret")
    return hashlib.sha256(token_secret.encode("utf-8")).hexdigest()


def create_session_token(session_id: int, secret_length: int = 32) -> Tuple[str, str]:
    """
    Create a new session token.

    Args:
        session_id: The session ID (snowflake)
        secret_length: Length of random secret in bytes

    Returns:
        Tuple of (full_token, secret_hash)
    """
    secret = generate_token_secret(secret_length)
    full_token = f"{session_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_bot_token(bot_id: int, secret_length: int = 48) -> Tuple[str, str]:
    """
    Create a new bot token.

    Bot tokens are longer-lived so use more entropy.

    Args:
        bot_id: The bot ID (snowflake)
        secret_length: Length of random secret in bytes

    Returns:
        Tuple of (full_token, secret_hash)
    """
    secret = generate_token_secret(secret_length)
    full_token = f"bot.{bot_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_email_token(token_id: int, secret_length: int = 32) -> Tuple[str, str]:
    """
    Create a token for email verification or password reset.

    Args:
        token_id: The token record ID
        secret_length: Length of random secret in bytes

    Returns:
        Tuple of (full_token, secret_hash)
    """
    secret = generate_token_secret(secret_length)
    full_token = f"email.{token_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def create_2fa_challenge_token(
    challenge_id: int, secret_length: int = 32
) -> Tuple[str, str]:
    """
    Create a temporary token for 2FA challenge.

    Args:
        challenge_id: The challenge record ID
        secret_length: Length of random secret in bytes

    Returns:
        Tuple of (full_token, secret_hash)
    """
    secret = generate_token_secret(secret_length)
    full_token = f"2fa.{challenge_id}.{secret}"
    secret_hash = hash_token(secret)
    return full_token, secret_hash


def parse_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Parse a token into its components.

    Args:
        token: The full token string

    Returns:
        Dict with token_type, id, and secret, or None if invalid format
    """
    if not token or not isinstance(token, str):
        return None

    parts = token.split(".")

    # Bot token: bot.<id>.<secret>
    if len(parts) == 3 and parts[0] == "bot":
        try:
            bot_id = int(parts[1])
            return {"token_type": "bot", "id": bot_id, "secret": parts[2]}
        except ValueError:
            return None

    # Email token: email.<id>.<secret>
    if len(parts) == 3 and parts[0] == "email":
        try:
            token_id = int(parts[1])
            return {"token_type": "email", "id": token_id, "secret": parts[2]}
        except ValueError:
            return None

    # 2FA challenge token: 2fa.<id>.<secret>
    if len(parts) == 3 and parts[0] == "2fa":
        try:
            challenge_id = int(parts[1])
            return {"token_type": "2fa", "id": challenge_id, "secret": parts[2]}
        except ValueError:
            return None

    # Session token: <id>.<secret>
    if len(parts) == 2:
        try:
            session_id = int(parts[0])
            return {"token_type": "session", "id": session_id, "secret": parts[1]}
        except ValueError:
            return None

    return None


def verify_token_hash(token_secret: str, stored_hash: str) -> bool:
    """
    Verify a token secret against its stored hash.

    Args:
        token_secret: The secret from the token
        stored_hash: The hash stored in the database

    Returns:
        True if the secret matches the hash
    """
    try:
        computed = hash_token(token_secret)
    except Exception:
        return False
    return _constant_time_compare(computed, stored_hash)


def _constant_time_compare(a: str, b: str) -> bool:
    """
    Compare two strings in constant time to prevent timing attacks.

    Args:
        a: First string
        b: Second string

    Returns:
        True if strings are equal
    """
    if len(a) != len(b):
        return False

    result = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        result |= x ^ y

    return result == 0
