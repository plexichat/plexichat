"""
OAuth login module.

This module handles login and registration via OAuth providers.
"""

from typing import Optional

from ..models import AuthResult
from .._lazy import _get_auth_manager


def oauth_login(
    provider: str,
    external_id: str,
    email: Optional[str] = None,
    username_hint: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    age: Optional[int] = None,
    dob: Optional[str] = None,
    email_verified: bool = False,
) -> AuthResult:
    """
    Login or register via OAuth provider.

    If user exists with this OAuth link, logs them in.
    If email matches existing user, links OAuth and logs in -- but only when
    ``email_verified`` is set and ``provider`` is a trusted provider; the
    linking boundary fails closed otherwise.
    Otherwise creates new account.
    """
    return (
        _get_auth_manager()
        .get_instance()
        .oauth_login(
            provider=provider,
            external_id=external_id,
            email=email,
            username_hint=username_hint,
            ip_address=ip_address,
            user_agent=user_agent,
            age=age,
            dob=dob,
            email_verified=email_verified,
        )
    )


__all__ = [
    "oauth_login",
]
