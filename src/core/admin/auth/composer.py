"""
Composer class that combines all auth mixins with the base class.
"""

from .base import AdminAuthBase
from .auth_mixin import AuthenticationMixin
from .otp_mixin import OTPMixin
from .otp_setup_mixin import OTPSetupMixin
from .password_mixin import PasswordMixin
from .security_mixin import SecurityMixin
from .session_mixin import SessionMixin


class AdminAuth(
    AuthenticationMixin,
    OTPMixin,
    SessionMixin,
    PasswordMixin,
    SecurityMixin,
    OTPSetupMixin,
    AdminAuthBase,
):
    """Complete admin authentication implementation composed from mixins."""

    __slots__ = ()
