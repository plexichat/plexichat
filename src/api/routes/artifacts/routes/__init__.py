"""Artifact route package.

Split by responsibility: CRUD, upload conversion, and export/op replay each
live in their own module. This package keeps the historical import surface
(``router``, the endpoint callables and the re-exported helpers) so
``from src.api.routes.artifacts.routes import ...`` continues to work.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.core.security.hardening import sanitize_header_filename

from ...artifacts_export import (
    _artifact_board_text,
    _artifact_export_html,
    _artifact_export_markdown,
    _artifact_export_odt,
    _artifact_export_pdf,
    _artifact_export_text,
    _artifact_native_bytes,
    _artifact_scribe_html,
    _artifact_script_text,
    _json_loads,
    _latin1_safe,
    _strip_html_markup,
    sanitize_rich_html,
)
from ...artifacts_helpers import (
    _AuthCache,
    _artifact_capability_for_type,
    _artifact_response,
    _author_profile_cache,
    _authorize_scope,
    _conversation_server_id,
    _deep_merge_payload,
    _emit_artifact_message,
    _get_manager,
    _is_conversation_member,
    _my_permissions,
    _require_artifact_capability,
    _require_server_permission,
    _validate_artifact_scope,
    payload_revision,
    stale_payload_write,
)
from ..constants import EXPORT_MIME_MAP
from ..validation import (
    parse_snowflake_id,
    safe_download_filename,
    validate_export_format,
)
from .common import _parse_optional_id, parse_artifact_id
from .crud import (
    create_artifact,
    delete_artifact,
    get_artifact,
    list_artifacts,
    update_artifact,
)
from .crud import router as _crud_router
from .exports import _export_artifact_impl, _list_artifact_ops_impl
from .exports import router as _exports_router
from .uploads import _convert_upload_impl
from .uploads import router as _uploads_router

# Literal sub-paths are registered before the dynamic ``/{artifact_id}``
# operations so a fixed path can never be shadowed by the id route. The route
# objects are lifted directly because ``include_router`` refuses a sub-router
# that has both an empty prefix and an empty path (e.g. ``POST ""``).
router = APIRouter()
router.routes.extend(_uploads_router.routes)
router.routes.extend(_exports_router.routes)
router.routes.extend(_crud_router.routes)
# Kept for the historical import surface: consumers read ``router.prefix``.
# The child routers already baked "/artifacts" into every route path, so this
# attribute is descriptive only and is not consulted when mounting.
router.prefix = "/artifacts"

__all__ = [
    "router",
    "sanitize_rich_html",
    "sanitize_header_filename",
    "_AuthCache",
    "_artifact_board_text",
    "_artifact_capability_for_type",
    "_artifact_export_html",
    "_artifact_export_markdown",
    "_artifact_export_odt",
    "_artifact_export_pdf",
    "_artifact_export_text",
    "_artifact_native_bytes",
    "_artifact_response",
    "_artifact_scribe_html",
    "_artifact_script_text",
    "_author_profile_cache",
    "_authorize_scope",
    "_conversation_server_id",
    "_convert_upload_impl",
    "_deep_merge_payload",
    "_emit_artifact_message",
    "_export_artifact_impl",
    "_get_manager",
    "_is_conversation_member",
    "_json_loads",
    "_latin1_safe",
    "_list_artifact_ops_impl",
    "_my_permissions",
    "_parse_optional_id",
    "_require_artifact_capability",
    "_require_server_permission",
    "_strip_html_markup",
    "_validate_artifact_scope",
    "create_artifact",
    "delete_artifact",
    "get_artifact",
    "list_artifacts",
    "parse_artifact_id",
    "parse_snowflake_id",
    "payload_revision",
    "safe_download_filename",
    "stale_payload_write",
    "update_artifact",
    "validate_export_format",
    "EXPORT_MIME_MAP",
]
