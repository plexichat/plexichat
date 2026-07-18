"""Template operations - create, get, update, delete, apply, preview templates."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import Server, ServerTemplate, TemplateData

_manager: Any = None


def _get_manager() -> Any:
    """Get the server manager instance."""
    global _manager
    if _manager is None:
        from . import _get_manager as _get_global_manager

        _manager = _get_global_manager()
    return _manager


def create_template(
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    name: str,
    description: Optional[str] = None,
) -> ServerTemplate:
    """Create a template from an existing server."""
    return _get_manager().create_template(user_id, server_id, name, description)


def get_template(
    code: str, user_id: Optional[SnowflakeID] = None
) -> Optional[ServerTemplate]:
    """Get a template by code."""
    return _get_manager().get_template(code, user_id)


def get_template_by_id(
    template_id: int, user_id: SnowflakeID
) -> Optional[ServerTemplate]:
    """Get a template by ID."""
    return _get_manager().get_template_by_id(template_id, user_id)


def get_user_templates(user_id: SnowflakeID, limit: int = 50) -> List[ServerTemplate]:
    """Get templates created by a user."""
    return _get_manager().get_user_templates(user_id, limit)


def get_public_templates(limit: int = 50) -> List[ServerTemplate]:
    """Get public templates."""
    return _get_manager().get_public_templates(limit)


def preview_template(code: str) -> Optional[TemplateData]:
    """Preview template data without applying."""
    return _get_manager().preview_template(code)


def apply_template(
    user_id: SnowflakeID,
    code: str,
    server_name: str,
    server_description: Optional[str] = None,
) -> Optional[Server]:
    """Apply a template to create a new server."""
    return _get_manager().apply_template(user_id, code, server_name, server_description)


def delete_template(user_id: SnowflakeID, code: str) -> bool:
    """Delete a template."""
    return _get_manager().delete_template(user_id, code)


def update_template(
    user_id: SnowflakeID,
    code: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_public: Optional[bool] = None,
) -> ServerTemplate:
    """Update template metadata."""
    return _get_manager().update_template(user_id, code, name, description, is_public)
