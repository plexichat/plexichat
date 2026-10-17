"""
OAuth2 submodule for application authorization.
"""

from .scopes import (
    VALID_SCOPES,
    SCOPE_DESCRIPTIONS,
    validate_scopes,
    parse_scopes,
    scopes_to_string,
)
from .tokens import (
    generate_authorization_code,
    generate_access_token,
    generate_refresh_token,
    hash_token,
    verify_token_hash,
)
from .flows import OAuth2Flow

__all__ = [
    "VALID_SCOPES",
    "SCOPE_DESCRIPTIONS",
    "validate_scopes",
    "parse_scopes",
    "scopes_to_string",
    "generate_authorization_code",
    "generate_access_token",
    "generate_refresh_token",
    "hash_token",
    "verify_token_hash",
    "OAuth2Flow",
]
