"""
Mixin providing CLI reference documentation page route handlers.
"""

from fastapi import Request


class CliPagesMixin:
    async def docs_cli_overview(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("cli/overview.md"),
            "CLI Reference",
            "/cli/overview",
        )
