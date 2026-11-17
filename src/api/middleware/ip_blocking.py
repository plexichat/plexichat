"""
IP Blocking Middleware - Intercepts requests from blacklisted IPs.
"""

from starlette.types import ASGIApp, Receive, Send, Scope
from typing import Any, Dict, cast

import utils.logger as logger

from .request_context import cached_client_ip


class IPBlockingMiddleware:
    """ASGI middleware to block blacklisted IP addresses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        import src.api as api
        from src.utils.net import get_client_ip

        # Use scope directly or handle per type. The resolved IP is shared
        # with logging/authentication/rate limiting for this request.
        client_ip = cached_client_ip(cast(Dict[str, Any], scope), resolver=get_client_ip)

        # Check if IP is blacklisted
        auth = api.get_auth()
        if auth and auth.is_ip_blocked(client_ip):
            logger.warning(
                f"Blocked {scope['type']} request from blacklisted IP: {client_ip}"
            )

            if scope["type"] == "websocket":
                await self._send_websocket_forbidden(send)
            else:
                await self._send_forbidden(scope.get("path", ""), send)
            return

        await self.app(scope, receive, send)

    async def _send_websocket_forbidden(self, send: Send) -> None:
        """Close WebSocket with forbidden code."""
        # 4003 is often used for forbidden/unauthorized in WS
        await send({"type": "websocket.close", "code": 4003, "reason": "IP Blocked"})

    async def _send_forbidden(self, path: str, send: Send) -> None:
        """Send a 403 Forbidden response with the canonical security policy.

        This middleware is registered outermost, so a blocked request never
        reaches ``SecurityHeadersMiddleware``. Without stamping here, every
        perimeter rejection escaped with no CSP, nosniff, framing or HSTS
        headers -- a security-header gap precisely on the hostile traffic the
        block list exists to stop. Reuse the same enforcement function the
        normal response path uses so the two can never drift apart.
        """
        response_body = b'{"error": {"code": 403, "message": "Access denied: your IP address is blocked."}}'

        from .response_policy import enforce_response_policy
        from .security import _resolve_api_prefix

        headers = enforce_response_policy(
            [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(response_body)).encode()),
            ],
            path=path,
            api_prefix=_resolve_api_prefix(),
        )

        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": response_body,
            }
        )
