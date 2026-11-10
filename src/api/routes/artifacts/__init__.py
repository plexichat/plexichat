"""Artifact routes package.

Drop-in replacement for the former monolithic ``src.api.routes.artifacts`` module.
Preserves the historical import surface (including helper exports and
``_export_artifact_impl`` / ``_convert_upload_impl`` / ``_list_artifact_ops_impl``)
for route registration, suite registry, and tests.
"""

from __future__ import annotations

from .routes import (  # noqa: F401
    _convert_upload_impl,
    _export_artifact_impl,
    _list_artifact_ops_impl,
    _parse_optional_id,
    create_artifact,
    delete_artifact,
    get_artifact,
    list_artifacts,
    router,
    update_artifact,
)
from .validation import (  # noqa: F401
    parse_snowflake_id,
    safe_download_filename,
    validate_export_format,
)

# Re-exported helpers: stable import surface for tests and sibling modules.
from ..artifacts_helpers import (  # noqa: F401
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
from ..artifacts_export import (  # noqa: F401
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

__all__ = [
    "router",
    "create_artifact",
    "list_artifacts",
    "get_artifact",
    "update_artifact",
    "delete_artifact",
    "_export_artifact_impl",
    "_convert_upload_impl",
    "_list_artifact_ops_impl",
    "_parse_optional_id",
    "parse_snowflake_id",
    "validate_export_format",
    "safe_download_filename",
    "sanitize_rich_html",
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
    "_deep_merge_payload",
    "_emit_artifact_message",
    "_get_manager",
    "_is_conversation_member",
    "_json_loads",
    "_latin1_safe",
    "_my_permissions",
    "_require_artifact_capability",
    "_require_server_permission",
    "_strip_html_markup",
    "_validate_artifact_scope",
    "payload_revision",
    "stale_payload_write",
]
