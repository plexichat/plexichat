"""
Mixin providing API reference documentation page route handlers.
"""

import re

from fastapi import HTTPException, Request

# A page name becomes a file name. Only the shapes the documentation files use
# are accepted, so ``..``, an encoded separator or an empty segment is a 404
# rather than a probe for something outside the docs tree.
_PAGE_NAME = re.compile(r"[a-z0-9-]+")


class ReferencePagesMixin:
    async def docs_features_page(self, request: Request, page: str):
        if not _PAGE_NAME.fullmatch(page):
            raise HTTPException(404, detail="Page not found")
        return await self._serve_page(
            request,
            self._doc_path(f"features/{page}.md"),
            page.replace("-", " ").title(),
            f"/features/{page}",
        )

    async def docs_oauth_scopes(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("oauth-scopes.md"),
            "OAuth Scopes",
            "/oauth-scopes",
        )

    async def docs_api_reference(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("api/index.md"), "API Reference", "/reference"
        )

    async def docs_api_page(self, request: Request, page: str):
        if not _PAGE_NAME.fullmatch(page):
            raise HTTPException(404, detail="Page not found")
        return await self._serve_page(
            request,
            self._doc_path(f"api/{page}.md"),
            page.title(),
            f"/reference/{page}",
        )

    async def docs_rate_limits(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("rate-limits.md"), "Rate Limits", "/rate-limits"
        )
