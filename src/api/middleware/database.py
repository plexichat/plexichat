"""
Database middleware - Ensures database connections are returned to the pool.
"""

from starlette.types import ASGIApp, Receive, Send, Scope
import src.api as api

class DatabaseMiddleware:
    """
    Middleware that ensures database connections are closed/returned to pool
    after each request is completed.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            # Force close the thread-local connection to return it to the pool
            try:
                db = api.get_db()
                if db:
                    db.close()
            except Exception:
                pass
