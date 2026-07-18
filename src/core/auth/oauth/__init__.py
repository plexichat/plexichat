"""
OAuth Security Module - Server-side state management and PKCE support.

This module provides secure OAuth2 authentication flows with:
- Server-side state storage and validation (CSRF protection)
- PKCE (Proof Key for Code Exchange) support for public clients
- Nonce support for OpenID Connect providers
- Automatic cleanup of expired states

Security features:
- State tokens are stored server-side with short TTL (10 minutes default)
- PKCE code verifiers are generated and stored securely
- Constant-time comparison for all token validation
- Automatic expiration of unused OAuth states
"""

from .state import (
    OAuthStateManager,
    OAuthState,
    create_oauth_state,
    verify_oauth_state,
    cleanup_expired_states,
)
from .pkce import (
    generate_pkce_pair,
    verify_pkce,
    PKCEChallenge,
)
from .login import oauth_login

__all__ = [
    "OAuthStateManager",
    "OAuthState",
    "create_oauth_state",
    "verify_oauth_state",
    "cleanup_expired_states",
    "generate_pkce_pair",
    "verify_pkce",
    "PKCEChallenge",
    "oauth_login",
]
