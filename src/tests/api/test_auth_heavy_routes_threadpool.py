"""Auth-heavy handlers must not block the event loop.

Password verification hashing and the synchronous DB calls behind login,
registration, logout and account deletion are blocking work. If those handlers
are changed back to ``async def`` without offloading the blocking calls,
FastAPI executes them on the event loop and even a trivial ``/ready`` probe
queues behind an Argon2 burst.
"""

import inspect

from src.api.routes.auth.login import login
from src.api.routes.auth.register import register
from src.api.routes.auth.sessions import logout
from src.api.routes.users.profile import ProfileMixin


def test_auth_heavy_routes_use_sync_handlers():
    handlers = [
        login,
        register,
        logout,
        ProfileMixin.update_current_user,
        ProfileMixin.schedule_account_deletion,
    ]
    for handler in handlers:
        assert not inspect.iscoroutinefunction(handler), (
            f"{handler.__qualname__} must stay a sync handler so FastAPI runs "
            "it in the worker threadpool"
        )
