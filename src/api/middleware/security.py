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

                # Per-path CSP scoping: the API surface is hardened
                # and only consumes JSON; the static-client bundle +
                # admin SPA rely on inline scripts/styles to bootstrap.
                # Emit a strict no-unsafe CSP for /api/v1/* so XSS in a
                # future JSON response can't ``eval``; fall back to the
                # historically-permissive policy for ``/``/``/admin``
                # and bundled SPA routes so we don't regress the
                # admin UI.
                path = scope.get("path", "")
                is_api = path.startswith("/api/v1/")
                is_media = path.startswith("/api/v1/media/")

                # Security Headers
                security_headers = [
                    (b"X-Content-Type-Options", b"nosniff"),
                    (b"X-XSS-Protection", b"1; mode=block"),
                    (b"Referrer-Policy", b"strict-origin-when-cross-origin"),
                    (b"X-Permitted-Cross-Domain-Policies", b"none"),
                ]
                if is_api and not is_media:
                    # Strict CSP for API responses. They should never
                    # be rendered as HTML; default-src 'none' makes
                    # any accidental script-injection inert.
                    security_headers.append(
                        (
                            b"Content-Security-Policy",
                            b"default-src 'none'; "
                            b"frame-ancestors 'none'; "
                            b"base-uri 'none'",
                        )
                    )
                elif is_media:
                    # Media responses can be embedded by third-party
                    # sites that have been signed; allow broad
                    # img-src and media-src, restrict scripts.
                    security_headers.append(
                        (
                            b"Content-Security-Policy",
                            b"default-src 'none'; "
                            b"img-src 'self' data: blob: *; "
                            b"media-src 'self' *; "
                            b"frame-ancestors *",
                        )
                    )
                else:
                    # Static-client / admin SPA bootstrap needs inline
                    # scripts + styles. The bundle is served from this
                    # origin only; CSP does not weaken XSS protection
                    # for the SPA surface, only legitimises the
                    # bootstrap-injection pattern that the Webpack
                    # dev fallback requires.
                    security_headers.append(
                        (
                            b"Content-Security-Policy",
                            b"default-src 'self'; "
                            b"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                            b"style-src 'self' 'unsafe-inline'; "
                            b"img-src 'self' data: blob:; "
                            b"font-src 'self' data:; "
                            b"connect-src 'self' ws: wss:; "
                            b"frame-ancestors 'self'; "
                            b"base-uri 'self'; "
                            b"form-action 'self'",
                        )
                    )

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

                # Relax CSP for media to allow embedding
                if is_media and b"content-security-policy" not in present_names:
                    final_headers.append(
                        (
                            b"Content-Security-Policy",
                            b"default-src 'self'; "
                            b"img-src 'self' data: blob: *; "
                            b"media-src 'self' *; "
                            b"frame-ancestors *",
                        )
                    )

                message["headers"] = final_headers

            await send(message)

        await self.app(scope, receive, send_with_security_headers)
