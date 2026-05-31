"""
Mixin providing WebSocket documentation page route handlers.
"""

from fastapi import Request


class WebSocketPagesMixin:
    async def docs_websocket_index(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("websocket/index.md"), "WebSocket", "/websocket"
        )

    async def docs_websocket_page(self, request: Request, page: str):
        return await self._serve_page(
            request,
            self._doc_path(f"websocket/{page}.md"),
            page.title(),
            f"/websocket/{page}",
        )

    async def docs_client_development(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("client-development/index.md"),
            "Client Development",
            "/client-development",
        )

    async def docs_client_websocket(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("client-development/websocket.md"),
            "Client WebSocket Development",
            "/client-development/websocket",
        )
