"""
Database middleware - Ensures database connections are cleaned up after requests.
SQLite connections are reused across requests; PostgreSQL connections are
returned to the pool.
"""

from starlette.types import ASGIApp, Receive, Send, Scope


class DatabaseMiddleware:
    """
    Middleware that cleans up the thread-local database connection after each
    request. SQLite connections are rolled back and kept for reuse on the same
    thread; PostgreSQL connections are returned to the pool.
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
            # Per-dialect cleanup. SQLite connections are thread-local and are
            # kept alive across requests so they don't pay connection setup
            # cost (PRAGMA re-setup, mmap remap, WAL churn) on every request;
            # PostgreSQL connections are returned to the pool.
            # Import inside to avoid circular dependency
            try:
                import src.api as api

                db = api.get_db()
                if db:
                    if db.type == "sqlite":
                        # Never leak a dangling transaction into the next
                        # request; roll it back but keep the connection.
                        if db.in_transaction or db.transaction_depth > 0:
                            db.rollback()
                    else:
                        db.close()
            except Exception:
                pass
