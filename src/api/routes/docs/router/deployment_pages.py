"""
Mixin providing deployment documentation page route handlers.
"""

from fastapi import Request


class DeploymentPagesMixin:
    async def docs_deployment(self, request: Request):
        return await self._serve_page(
            request, self._doc_path("deployment/index.md"), "Deployment", "/deployment"
        )

    async def docs_deployment_overview(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/overview.md"),
            "Deployment Overview",
            "/deployment/overview",
        )

    async def docs_deployment_requirements(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/requirements.md"),
            "Deployment Requirements",
            "/deployment/requirements",
        )

    async def docs_deployment_getting_started(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/getting-started.md"),
            "Deployment Getting Started",
            "/deployment/getting-started",
        )

    async def docs_deployment_index(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/index.md"),
            "Deployment Index",
            "/deployment/index",
        )

    async def docs_deployment_postgres_migration(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/postgres-migration.md"),
            "PostgreSQL Migration Guide",
            "/deployment/postgres-migration",
        )

    async def docs_deployment_versioning(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("deployment/versioning.md"),
            "Versioning and Updates",
            "/deployment/versioning",
        )

    async def docs_migrations(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("migrations.md"),
            "Database Migrations Guide",
            "/migrations",
        )

    async def docs_migration_reference(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("migration-reference.md"),
            "Migration Reference",
            "/migration-reference",
        )
