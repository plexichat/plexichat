"""
Mixin providing configuration documentation page route handlers.
"""

from fastapi import Request


class ConfigPagesMixin:
    async def docs_configuration(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("configuration.md"),
            "Configuration",
            "/configuration",
        )

    async def docs_config_authentication(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-authentication.md"),
            "Authentication Configuration",
            "/config-authentication",
        )

    async def docs_config_database(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-database.md"),
            "Database Configuration",
            "/config-database",
        )

    async def docs_config_redis(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-redis.md"),
            "Redis Configuration",
            "/config-redis",
        )

    async def docs_config_media(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-media.md"),
            "Media Configuration",
            "/config-media",
        )

    async def docs_config_voice(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-voice.md"),
            "Voice Configuration",
            "/config-voice",
        )

    async def docs_config_websocket(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-websocket.md"),
            "WebSocket Configuration",
            "/config-websocket",
        )

    async def docs_config_search(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-search.md"),
            "Search Configuration",
            "/config-search",
        )

    async def docs_config_rate_limiting(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-rate-limiting.md"),
            "Rate Limiting Configuration",
            "/config-rate-limiting",
        )

    async def docs_config_api(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-api.md"),
            "API & Server Configuration",
            "/config-api",
        )

    async def docs_config_email(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-email.md"),
            "Email Configuration",
            "/config-email",
        )

    async def docs_config_embeds(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/configuration/config-embeds.md"),
            "Embeds Configuration",
            "/config-embeds",
        )
