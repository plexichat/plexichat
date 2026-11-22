"""Bridge synchronous auth revocation to server-side gateway socket closure.

The gateway authenticates a WebSocket only at IDENTIFY/RESUME. A
``SECURITY_ALERT`` / ``SECURITY_LOGOUT`` *frame* is still a client-side hint,
but the corresponding cross-worker event now also closes the receiving worker's
local sockets server-side (see ``GatewayDispatcher.dispatch_remote_event``).
Closure is always worker-local -- the dispatcher only knows sockets in its own
process -- so the revocation event is consumed on every worker to close each
one's local sockets. This is what makes a revoked/banned user's already-open
socket fail closed on a multi-worker deployment instead of depending on the
client honouring a hint. The admin force-logout/lock routes close that socket
directly, but ordinary
revocation lives in synchronous core code (password change/reset, mass logout,
the account reaper) and in FastAPI worker threads, so it cannot simply
``await`` the dispatcher.

:func:`close_user_gateway_connections` schedules the dispatcher's close on the
process-wide serving loop and is strictly non-raising: the DB write that
revoked the credential has already committed, so a gateway failure must not
turn a completed revocation into a 500.
"""

import asyncio
from typing import Optional

import utils.logger as logger


def _gateway_loop() -> Optional[asyncio.AbstractEventLoop]:
    """Return a live loop to schedule on: current running loop or serving loop.

    Auth code runs both on the gateway loop (async route handlers calling sync
    helpers) and off it (FastAPI worker threads, the reaper daemon). Prefer the
    current thread's running loop, then fall back to the process-wide serving
    loop recorded by :meth:`GatewayDispatcher.set_event_loop`.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    try:
        from src.api.websocket.dispatcher import _serving_loop

        return _serving_loop()
    except Exception:
        return None


def close_user_gateway_connections(
    user_id: int, reason: str = "Session revoked"
) -> None:
    """Close every gateway connection belonging to ``user_id`` on THIS worker.

    Non-raising, best-effort, and safe to call from a synchronous context on or
    off the serving loop. The dispatcher closes with a non-resumable code by
    default, so a revoked session cannot resume into a fresh authenticated
    socket.

    Closure is worker-local: the dispatcher only knows about sockets in this
    process. On a multi-worker deployment the revocation event is published to
    every worker, and each worker's ``dispatch_remote_event`` closes its own
    matching local sockets, so an already-open socket on a sibling worker no
    longer stays authenticated after the client-side hint. When the emitter
    supplies revoked session ids the close is bound to those exact sessions,
    making it idempotent and safe against replay (a socket created under a
    newer session id cannot be closed by an old event). Re-authentication is
    independently blocked on every worker because revocation deletes the
    persisted sessions and invalidates the shared ``token_verify`` cache.
    """
    loop = _gateway_loop()
    if loop is None or loop.is_closed():
        logger.debug(
            "No live gateway loop; skipping socket close for revoked user %s",
            user_id,
        )
        return

    async def _close() -> None:
        try:
            from src.api.websocket import get_dispatcher, is_setup

            if not is_setup():
                return
            await get_dispatcher().close_user_connections(
                user_id=int(user_id), reason=reason
            )
        except Exception:
            logger.warning(
                "Failed to close gateway connections for revoked user %s",
                user_id,
                exc_info=True,
            )

    try:
        asyncio.run_coroutine_threadsafe(_close(), loop)
    except Exception:
        logger.warning(
            "Failed to schedule gateway socket close for revoked user %s",
            user_id,
            exc_info=True,
        )


def close_session_gateway_connections(
    user_id: int,
    auth_session_id: int,
    reason: str = "Session revoked",
) -> None:
    """Close only the gateway sockets bound to one revoked auth session.

    Non-raising and callable from a synchronous context on or off the serving
    loop, like :func:`close_user_gateway_connections`. Matching is done by the
    dispatcher on the verified ``TokenInfo.session_id`` recorded on each socket
    at IDENTIFY/RESUME, so a sibling session on the same user is left open. The
    revocation event carries that same session id from the authoritative
    revocation helper, so sibling workers close only this session's sockets
    server-side instead of relying on a client-side hint.
    """
    loop = _gateway_loop()
    if loop is None or loop.is_closed():
        logger.debug(
            "No live gateway loop; skipping socket close for revoked session %s",
            auth_session_id,
        )
        return

    async def _close() -> None:
        try:
            from src.api.websocket import get_dispatcher, is_setup

            if not is_setup():
                return
            await get_dispatcher().close_session_connections(
                user_id=int(user_id),
                auth_session_id=int(auth_session_id),
                reason=reason,
            )
        except Exception:
            logger.warning(
                "Failed to close gateway connections for revoked session %s",
                auth_session_id,
                exc_info=True,
            )

    try:
        asyncio.run_coroutine_threadsafe(_close(), loop)
    except Exception:
        logger.warning(
            "Failed to schedule gateway socket close for revoked session %s",
            auth_session_id,
            exc_info=True,
        )
