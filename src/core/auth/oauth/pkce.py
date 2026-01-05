"""
PKCE (Proof Key for Code Exchange) implementation.

PKCE is an extension to OAuth2 that prevents authorization code interception attacks.
It's especially important for public clients (mobile apps, SPAs) but is now
recommended for all OAuth2 clients per RFC 7636 and OAuth 2.1.

Flow:
1. Client generates a random code_verifier (43-128 chars)
2. Client creates code_challenge = BASE64URL(SHA256(code_verifier))
3. Client sends code_challenge with authorization request
4. After callback, client sends code_verifier with token exchange
5. Server verifies SHA256(code_verifier) matches stored code_challenge

Configuration (in oauth section of config.yaml):
    pkce:
        verifier_length: 64      # Length of random bytes for verifier (32-96)
        min_verifier_length: 43  # Minimum verifier length per RFC 7636
        max_verifier_length: 128 # Maximum verifier length per RFC 7636
"""

import os
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional, Dict, Any

import utils.logger as logger


# Default configuration values
DEFAULT_VERIFIER_LENGTH = 64
DEFAULT_MIN_VERIFIER_LENGTH = 43
DEFAULT_MAX_VERIFIER_LENGTH = 128


@dataclass
class PKCEChallenge:
    """PKCE challenge pair for OAuth2 authorization."""
    
    code_verifier: str
    """The secret verifier (43-128 URL-safe chars). Keep this secret until token exchange."""
    
    code_challenge: str
    """The challenge to send with authorization request (BASE64URL(SHA256(verifier)))."""
    
    code_challenge_method: str = "S256"
    """The challenge method. Always S256 (SHA-256) for security."""


def generate_pkce_pair(
    verifier_length: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
) -> PKCEChallenge:
    """
    Generate a PKCE code verifier and challenge pair.
    
    Args:
        verifier_length: Length of random bytes for verifier (default from config or 64).
                        Results in ~86 character verifier after base64 encoding.
                        Must produce verifier between 43-128 chars per RFC 7636.
        config: Optional PKCE configuration dict with keys:
                - verifier_length: Default length of random bytes
                - min_verifier_length: Minimum verifier length (default 43)
                - max_verifier_length: Maximum verifier length (default 128)
    
    Returns:
        PKCEChallenge with code_verifier and code_challenge.
    
    Security:
        - Uses os.urandom for cryptographically secure random bytes
        - SHA-256 for challenge derivation (S256 method)
        - URL-safe base64 encoding without padding
    """
    # Get configuration values
    cfg = config or {}
    default_length = cfg.get("verifier_length", DEFAULT_VERIFIER_LENGTH)
    min_length = cfg.get("min_verifier_length", DEFAULT_MIN_VERIFIER_LENGTH)
    max_length = cfg.get("max_verifier_length", DEFAULT_MAX_VERIFIER_LENGTH)
    
    # Use provided length or default
    if verifier_length is None:
        verifier_length = default_length
    
    # Enforce security bounds
    if verifier_length < 32:
        logger.debug(f"PKCE verifier_length {verifier_length} too small, using 32")
        verifier_length = 32  # Minimum for security
    if verifier_length > 96:
        logger.debug(f"PKCE verifier_length {verifier_length} too large, using 96")
        verifier_length = 96  # Keep verifier under 128 chars
    
    # Generate random bytes and encode as URL-safe base64
    random_bytes = os.urandom(verifier_length)
    code_verifier = base64.urlsafe_b64encode(random_bytes).decode("utf-8").rstrip("=")
    
    # Ensure verifier is within RFC 7636 bounds (43-128 chars)
    if len(code_verifier) < min_length:
        # Pad with more random chars if needed (shouldn't happen with length >= 32)
        logger.debug(f"PKCE verifier too short ({len(code_verifier)}), padding to {min_length}")
        extra = secrets.token_urlsafe(16)
        code_verifier = code_verifier + extra
    
    if len(code_verifier) > max_length:
        logger.debug(f"PKCE verifier too long ({len(code_verifier)}), truncating to {max_length}")
        code_verifier = code_verifier[:max_length]
    
    # Generate challenge: BASE64URL(SHA256(verifier))
    code_challenge = _create_s256_challenge(code_verifier)
    
    logger.debug(f"Generated PKCE pair: verifier_len={len(code_verifier)}, challenge_len={len(code_challenge)}")
    
    return PKCEChallenge(
        code_verifier=code_verifier,
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )


def verify_pkce(
    code_verifier: str,
    stored_challenge: str,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Verify a PKCE code verifier against the stored challenge.
    
    Args:
        code_verifier: The verifier sent during token exchange.
        stored_challenge: The challenge that was stored during authorization.
        config: Optional PKCE configuration dict with keys:
                - min_verifier_length: Minimum verifier length (default 43)
                - max_verifier_length: Maximum verifier length (default 128)
    
    Returns:
        True if SHA256(code_verifier) matches stored_challenge.
    
    Security:
        Uses constant-time comparison to prevent timing attacks.
    """
    if not code_verifier or not stored_challenge:
        logger.debug("PKCE verification failed: empty verifier or challenge")
        return False
    
    # Get configuration values
    cfg = config or {}
    min_length = cfg.get("min_verifier_length", DEFAULT_MIN_VERIFIER_LENGTH)
    max_length = cfg.get("max_verifier_length", DEFAULT_MAX_VERIFIER_LENGTH)
    
    # Validate verifier length per RFC 7636
    if len(code_verifier) < min_length or len(code_verifier) > max_length:
        logger.debug(f"PKCE verification failed: verifier length {len(code_verifier)} outside bounds [{min_length}, {max_length}]")
        return False
    
    # Compute challenge from verifier
    computed_challenge = _create_s256_challenge(code_verifier)
    
    # Constant-time comparison
    result = secrets.compare_digest(computed_challenge, stored_challenge)
    
    if not result:
        logger.debug("PKCE verification failed: challenge mismatch")
    else:
        logger.debug("PKCE verification successful")
    
    return result


def _create_s256_challenge(code_verifier: str) -> str:
    """
    Create S256 challenge from verifier.
    
    Args:
        code_verifier: The code verifier string.
    
    Returns:
        BASE64URL(SHA256(ASCII(code_verifier))) without padding.
    """
    # SHA-256 hash of the ASCII verifier
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    
    # BASE64URL encode without padding
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    
    return challenge
