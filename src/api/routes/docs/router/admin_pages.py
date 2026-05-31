"""
Mixin providing admin documentation page route handlers.
"""

from fastapi import Request


class AdminPagesMixin:
    async def docs_admin(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/index.md"),
            "Admin Documentation",
            "/admin",
        )

    async def docs_admin_getting_started(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/getting-started.md"),
            "Admin Getting Started",
            "/admin/getting-started",
        )

    async def docs_admin_approval_workflows(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/approval-workflows.md"),
            "Approval Workflows",
            "/admin/approval-workflows",
        )

    async def docs_admin_audit_logging(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/audit-logging.md"),
            "Audit Logging",
            "/admin/audit-logging",
        )

    async def docs_admin_rbac(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/rbac.md"),
            "Role-Based Access Control",
            "/admin/rbac",
        )

    async def docs_admin_server_management(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/server-management.md"),
            "Server Management",
            "/admin/server-management",
        )

    async def docs_admin_operations(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/operations.md"),
            "Operations",
            "/admin/operations",
        )

    async def docs_admin_troubleshooting(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/troubleshooting.md"),
            "Troubleshooting",
            "/admin/troubleshooting",
        )

    async def docs_admin_user_management(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/user-management.md"),
            "User Management",
            "/admin/user-management",
        )

    async def docs_admin_security(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("admin/security.md"),
            "Admin Security",
            "/admin/security",
        )
