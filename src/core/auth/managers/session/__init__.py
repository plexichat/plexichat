"""Session handling for :class:`AuthManager`.

The historical ``managers/session.py`` module became this package so each part
has one concern: login and failure bookkeeping, token verification, gateway
tickets, and the session lifecycle. ``SessionMixin`` composes them, so the
import path ``src.core.auth.managers.session`` and every public method keep
working unchanged.
"""

from .lifecycle import LifecycleMixin
from .login import LoginMixin
from .tickets import TicketMixin
from .verification import VerificationMixin


class SessionMixin(LoginMixin, VerificationMixin, TicketMixin, LifecycleMixin):
    """Compose the session mixins into the class ``AuthManager`` inherits."""


__all__ = ["SessionMixin"]
