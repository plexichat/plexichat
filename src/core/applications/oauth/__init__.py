"""
OAuth2 submodule for application authorization.
"""

from .scopes import (
    VALID_SCOPES,
    SCOPE_DESCRIPTIONS,
    validate_scopes,
    require_allowed_scopes,
    scopes_are_subset,
    parse_scopes,
    scopes_to_string,
)
from .tokens import (
    generate_authorization_code,
    generate_access_token,
    generate_refresh_token,
    hash_token,
    verify_token_hash,
    create_s256_challenge,
    verify_pkce_challenge,
    validate_pkce_challenge,
    validate_pkce_verifier,
)
from .flows import OAuth2Flow

__all__ = [
    "VALID_SCOPES",
    "SCOPE_DESCRIPTIONS",
    "validate_scopes",
    "require_allowed_scopes",
    "scopes_are_subset",
    "parse_scopes",
    "scopes_to_string",
    "generate_authorization_code",
    "generate_access_token",
    "generate_refresh_token",
    "hash_token",
    "verify_token_hash",
    "create_s256_challenge",
    "verify_pkce_challenge",
    "validate_pkce_challenge",
    "validate_pkce_verifier",
    "OAuth2Flow",
]
