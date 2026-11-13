"""Artifact routes package.

Drop-in replacement for the former monolithic ``src.api.routes.artifacts`` module.
Preserves the historical import surface (including helper exports and
``_export_artifact_impl`` / ``_convert_upload_impl`` / ``_list_artifact_ops_impl``)
for route registration, suite registry, and tests.
"""

from __future__ import annotations

from .routes import (
    _convert_upload_impl as _convert_upload_impl,
    _export_artifact_impl as _export_artifact_impl,
    _list_artifact_ops_impl as _list_artifact_ops_impl,
    _parse_optional_id as _parse_optional_id,
    create_artifact as create_artifact,
    delete_artifact as delete_artifact,
    get_artifact as get_artifact,
    list_artifacts as list_artifacts,
    router as router,
    update_artifact as update_artifact,
)
from .validation import (
    parse_snowflake_id as parse_snowflake_id,
    safe_download_filename as safe_download_filename,
    validate_export_format as validate_export_format,
)

# Re-exported helpers: stable import surface for tests and sibling modules.
from ..artifacts_helpers import (
    _artifact_capability_for_type as _artifact_capability_for_type,
    _artifact_response as _artifact_response,
    _author_profile_cache as _author_profile_cache,
    _authorize_scope as _authorize_scope,
    _conversation_server_id as _conversation_server_id,
    _deep_merge_payload as _deep_merge_payload,
    _emit_artifact_message as _emit_artifact_message,
    _get_manager as _get_manager,
    _is_conversation_member as _is_conversation_member,
    _my_permissions as _my_permissions,
    _require_artifact_capability as _require_artifact_capability,
    _require_server_permission as _require_server_permission,
    _validate_artifact_scope as _validate_artifact_scope,
    payload_revision as payload_revision,
    stale_payload_write as stale_payload_write,
)
from ..artifacts_export import (
    _artifact_board_text as _artifact_board_text,
    _artifact_export_html as _artifact_export_html,
    _artifact_export_markdown as _artifact_export_markdown,
    _artifact_export_odt as _artifact_export_odt,
    _artifact_export_pdf as _artifact_export_pdf,
    _artifact_export_text as _artifact_export_text,
    _artifact_native_bytes as _artifact_native_bytes,
    _artifact_scribe_html as _artifact_scribe_html,
    _artifact_script_text as _artifact_script_text,
    _json_loads as _json_loads,
    _latin1_safe as _latin1_safe,
    _strip_html_markup as _strip_html_markup,
    sanitize_rich_html as sanitize_rich_html,
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
