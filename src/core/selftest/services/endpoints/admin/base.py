"""Shared admin-tester helpers and base class.

The admin endpoint mixins all perform the same multi-admin approval flow, so
the ceremony wrappers live here instead of being duplicated or imported
between sibling mixins.
"""

from ..base import EndpointTesterBase


class AdminMixinBase(EndpointTesterBase):
    """Approval-ceremony wrappers shared by the admin endpoint mixins."""

    def _third_user_session(self):
        from src.core.selftest.services.approval_ceremony import (
            third_user_session as _shared_third,
        )

        return _shared_third(self.ctx)

    def _ensure_user_session(self, session, username: str) -> bool:
        from src.core.selftest.services.approval_ceremony import (
            ensure_user_session as _shared_ensure,
        )

        return bool(_shared_ensure(self.ctx, session, username))

    def _approval_ceremony(self, action_type: str):
        from src.core.selftest.services.approval_ceremony import (
            run_ceremony as _shared_ceremony,
        )

        return bool(_shared_ceremony(self.ctx, action_type))
