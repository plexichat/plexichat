"""
IP Blocking Middleware - Intercepts requests from blacklisted IPs.
"""

from starlette.types import ASGIApp, Receive, Send, Scope
from typing import Any, Dict, cast

import utils.logger as logger


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
        
        # Use scope directly or handle per type
        client_ip = get_client_ip(cast(Dict[str, Any], scope))

        # Check if IP is blacklisted
        auth = api.get_auth()
        if auth and auth.is_ip_blocked(client_ip):
            logger.warning(f"Blocked {scope['type']} request from blacklisted IP: {client_ip}")
            
            if scope["type"] == "websocket":
                await self._send_websocket_forbidden(send)
            else:
                await self._send_forbidden(send)
            return

        await self.app(scope, receive, send)

    async def _send_websocket_forbidden(self, send: Send) -> None:
        """Close WebSocket with forbidden code."""
        # 4003 is often used for forbidden/unauthorized in WS
        await send({
            "type": "websocket.close",
            "code": 4003,
            "reason": "IP Blocked"
        })

    async def _send_forbidden(self, send: Send) -> None:
        """Send a 403 Forbidden response."""
        response_body = b'{"error": {"code": 403, "message": "Access denied: your IP address is blocked."}}'
        
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(response_body)).encode()),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": response_body,
        })
