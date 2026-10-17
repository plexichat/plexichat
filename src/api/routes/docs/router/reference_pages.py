"""
Mixin providing API reference documentation page route handlers.
"""

from fastapi import Request


class ReferencePagesMixin:
    async def docs_api_reference(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("api/index.md"), "API Reference", "/reference"
        )

    async def docs_api_page(self, request: Request, page: str):
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

    async def docs_api_notifications(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/notifications.md"),
            "Notifications API",
            "/reference/notifications",
        )

    async def docs_api_polls(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/polls.md"),
            "Polls API",
            "/reference/polls",
        )

    async def docs_api_voice(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/voice.md"),
            "Voice API",
            "/reference/voice",
        )

    async def docs_api_media(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/media.md"),
            "Media API",
            "/reference/media",
        )

    async def docs_api_reports(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/reports.md"),
            "Reports API",
            "/reference/reports",
        )

    async def docs_api_feedback(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/feedback.md"),
            "Feedback API",
            "/reference/feedback",
        )

    async def docs_api_telemetry(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/telemetry.md"),
            "Telemetry API",
            "/reference/telemetry",
        )

    async def docs_api_system(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/system.md"),
            "System API",
            "/reference/system",
        )

    async def docs_api_admin(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("api/admin.md"),
            "Admin API",
            "/reference/admin",
        )
