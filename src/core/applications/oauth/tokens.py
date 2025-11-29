"""
OAuth2 tokens - Token generation and verification.
"""

import secrets
import hashlib
from typing import Tuple


def generate_authorization_code(code_id: int, length: int = 32) -> Tuple[str, str]:
    """
    Generate an authorization code.
    
    Args:
        code_id: ID of the authorization code record
        length: Length of random bytes
        
    Returns:
        Tuple of (full_code, code_hash)
    """
    random_bytes = secrets.token_urlsafe(length)
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
    random_bytes = secrets.token_urlsafe(length)
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
    random_bytes = secrets.token_urlsafe(length)
    full_token = f"refresh.{token_id}.{random_bytes}"
    token_hash = hash_token(random_bytes)
    return full_token, token_hash


def generate_interaction_token(interaction_id: int, length: int = 32) -> Tuple[str, str]:
    """
    Generate an interaction token.
    
    Args:
        interaction_id: ID of the interaction record
        length: Length of random bytes
        
    Returns:
        Tuple of (full_token, token_hash)
    """
    random_bytes = secrets.token_urlsafe(length)
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
    secret = secrets.token_urlsafe(length)
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
    actual_hash = hash_token(token)
    return secrets.compare_digest(actual_hash, expected_hash)


def parse_oauth_token(token: str) -> dict:
    """
    Parse an OAuth token string.
    
    Args:
        token: Full token string
        
    Returns:
        Dict with token_type, id, and secret, or None if invalid
    """
    if not token:
        return None
    
    parts = token.split(".", 2)
    if len(parts) != 3:
        return None
    
    token_type, token_id, secret = parts
    
    if token_type not in ("auth", "access", "refresh", "int"):
        return None
    
    try:
        token_id = int(token_id)
    except ValueError:
        return None
    
    return {
        "token_type": token_type,
        "id": token_id,
        "secret": secret,
    }
