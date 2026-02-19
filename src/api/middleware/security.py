"""
Security headers middleware - Adds standard security headers to all responses.
"""

from starlette.types import ASGIApp, Receive, Send, Scope


class SecurityHeadersMiddleware:
    """ASGI middleware to add security headers to all HTTP responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))

                # Check which headers are already present
                present_headers = {h[0].lower() for h in headers}

                # Check if this is a media attachment request
                path = scope.get("path", "")
                is_media = path.startswith("/api/v1/media/")

                # Security Headers
                security_headers = [
                    (b"X-Content-Type-Options", b"nosniff"),
                    (b"X-XSS-Protection", b"1; mode=block"),
                    (b"Referrer-Policy", b"strict-origin-when-cross-origin"),
                    (b"X-Permitted-Cross-Domain-Policies", b"none"),
                ]

                # Only add if not already present (to avoid duplication)
                final_headers = []
                for h_name, h_value in headers:
                    final_headers.append((h_name, h_value))

                present_names = {h[0].lower() for h in final_headers}

                for name, value in security_headers:
                    if name.lower() not in present_names:
                        final_headers.append((name, value))

                # Add X-Frame-Options: SAMEORIGIN only if not a media request and not present
                if not is_media and b"x-frame-options" not in present_names:
                    final_headers.append((b"X-Frame-Options", b"SAMEORIGIN"))

                message["headers"] = final_headers

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
