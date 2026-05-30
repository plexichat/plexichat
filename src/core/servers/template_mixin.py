"""Template operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Server, ServerTemplate, TemplateData


class TemplateMixin:
    """Mixin for template operations.

    Provides: create_template, get_template, get_template_by_id, get_user_templates,
    get_public_templates, preview_template, apply_template, delete_template, update_template
    """

    _template_manager: Any = None

    def create_template(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        description: Optional[str] = None,
    ) -> ServerTemplate:
        """Create a template from an existing server."""
        return self._template_manager.create_template(
            user_id, server_id, name, description
        )

    def get_template(
        self, code: str, user_id: Optional[SnowflakeID] = None
    ) -> Optional[ServerTemplate]:
        """Get a template by code."""
        return self._template_manager.get_template(code, user_id)

    def get_template_by_id(
        self, template_id: int, user_id: SnowflakeID
    ) -> Optional[ServerTemplate]:
        """Get a template by ID."""
        return self._template_manager.get_template_by_id(template_id, user_id)

    def get_user_templates(
        self, user_id: SnowflakeID, limit: int = 50
    ) -> List[ServerTemplate]:
        """Get templates created by a user."""
        return self._template_manager.get_user_templates(user_id, limit)

    def get_public_templates(self, limit: int = 50) -> List[ServerTemplate]:
        """Get public templates."""
        return self._template_manager.get_public_templates(limit)

    def preview_template(self, code: str) -> Optional[TemplateData]:
        """Preview template data without applying."""
        return self._template_manager.preview_template(code)

    def apply_template(
        self,
        user_id: SnowflakeID,
        code: str,
        server_name: str,
        server_description: Optional[str] = None,
    ) -> Optional[Server]:
        """Apply a template to create a new server."""
        return self._template_manager.apply_template(
            user_id, code, server_name, server_description
        )

    def delete_template(self, user_id: SnowflakeID, code: str) -> bool:
        """Delete a template."""
        return self._template_manager.delete_template(user_id, code)

    def update_template(
        self,
        user_id: SnowflakeID,
        code: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_public: Optional[bool] = None,
    ) -> ServerTemplate:
        """Update template metadata."""
        return self._template_manager.update_template(
            user_id, code, name, description, is_public
        )
