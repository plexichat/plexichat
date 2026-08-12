"""
Database middleware - Ensures database connections are cleaned up after requests.
"""

from starlette.types import ASGIApp, Receive, Send, Scope


class DatabaseMiddleware:
    """
    Middleware that cleans up the thread-local database connection after each
    request.

    PostgreSQL connections are returned to the external pool and removed from
    thread-local so the next request on that worker gets a fresh pool checkout.

    SQLite connections are kept alive in thread-local across requests. Only
    dangling transactions are rolled back. This avoids repeated
    ``sqlite3.connect()`` + PRAGMA re-initialization on every request and
    lets the ``SqlitePool`` actually reduce physical connection churn.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        finally:
            try:
                import src.api as api

                db = api.get_db()
                if db:
                    if db.type == "postgres":
                        db.close()
                    else:
                        if db.in_transaction or db.transaction_depth > 0:
                            db.rollback()
            except Exception:
                pass
