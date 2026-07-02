"""
Mixin providing core documentation page route handlers.
"""

from fastapi import Request


class CorePagesMixin:
    async def docs_index(self, request: Request):
        return await self._serve_page(request, self._doc_path("index.md"), "Home", "/")

    async def docs_getting_started(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("getting-started.md"),
            "Getting Started",
            "/getting-started",
        )

    async def docs_features(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("features.md"), "Features", "/features"
        )

    async def docs_permissions(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/permissions.md"),
            "Permissions",
            "/permissions",
        )

    async def docs_security(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("security.md"), "Security", "/security"
        )

    async def docs_keyrings(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("keyrings.md"),
            "Keyrings and KEK Migration",
            "/keyrings",
        )

    async def docs_performance(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("performance.md"), "Performance", "/performance"
        )

    async def docs_data_types(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("data-types.md"), "Data Types", "/data-types"
        )

    async def docs_default_config(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("default-config.md"),
            "Default Configuration",
            "/default-config",
        )

    async def docs_errors(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("errors.md"), "Errors", "/errors"
        )
