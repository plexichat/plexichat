"""
IP Blocking Middleware - Intercepts requests from blacklisted IPs.
"""

from fastapi import Request
from starlette.types import ASGIApp, Receive, Send, Scope

import utils.logger as logger


class IPBlockingMiddleware:
    """ASGI middleware to block blacklisted IP addresses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import src.api as api
        # Extract IP without consuming 'receive' stream
        from src.utils.net import get_client_ip
        
        # We can create a Request object with a dummy receive to avoid consuming the stream
        # Or just use the scope directly if get_client_ip supports it
        request = Request(scope)
        client_ip = get_client_ip(request)

        # Check if IP is blacklisted
        auth = api.get_auth()
        if auth and auth.is_ip_blocked(client_ip):
            logger.warning(f"Blocked request from blacklisted IP: {client_ip}")
            
            # Send 403 Forbidden response
            await self._send_forbidden(send)
            return

        await self.app(scope, receive, send)

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
