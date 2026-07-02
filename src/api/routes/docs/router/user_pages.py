"""
Mixin providing end-user documentation page route handlers.
"""

from fastapi import Request


class UserPagesMixin:
    async def docs_end_user_getting_started(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/getting-started.md"),
            "User Guide: Getting Started",
            "/end-user/getting-started",
        )

    async def docs_end_user_index(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/index.md"),
            "End User Documentation",
            "/end-user",
        )

    async def docs_end_user_passkeys(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/passkeys.md"),
            "Passkeys",
            "/end-user/passkeys",
        )

    async def docs_end_user_password_guidance(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/password-guidance.md"),
            "Password Guidance",
            "/end-user/password-guidance",
        )

    async def docs_end_user_permissions(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/permissions.md"),
            "Permissions",
            "/end-user/permissions",
        )

    async def docs_end_user_2fa(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/two-factor-authentication.md"),
            "Two-Factor Authentication",
            "/end-user/two-factor-authentication",
        )
