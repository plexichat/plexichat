"""Shared member surface for the SUM manager mixins.

The manager implementation is split across ``manager_*.py`` mixin modules
composed by ``SumManager``. Each mixin relies on helpers and state defined on
a sibling mixin or on ``BaseManager``; this protocol declares that shared
surface so every mixin type-checks against it (same pattern as the voice
manager protocol). Real implementations always win over these declarations:
in the composed ``SumManager`` MRO every mixin precedes this class.
"""

from typing import Any, Callable

from src.core.base import SnowflakeID


class SumManagerProtocol:
    # State owned by BaseManager / SumManagerBase construction. Members
    # provided by BaseManager are declared as annotations only so the
    # protocol never shadows the real implementation in the composed MRO.
    _db: Any = None
    _generate_id: Callable[[], SnowflakeID]
    _servers: Any = None
    _media: Any = None
    _blob: Any = None

    # Caps re-exported per-class on SumManager.
    MAX_OPERATION_CELLS: int = 0
    MAX_VIEWPORT_CELLS: int = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    @staticmethod
    def _session_lock(workbook_id: int) -> Any:
        raise NotImplementedError

    def get_workbook(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def apply_operation(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def import_csv(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def list_presence(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def get_freeze(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _audit(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _authorize(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _authorized_user_ids(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _blob_bytes(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _bump_statistics(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _cache_current_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _cells_to_csv(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _check_range_permissions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _check_structural_permission(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _close_sqlite(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _close_sqlite_memory(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _current_revision(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _decode(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _delete_blob_confirmed(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _drop_session(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _emit_update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _ensure_axis_metadata(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _ensure_sheet(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _evict_sessions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _extract_mentions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _feature_dict(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _feature_row(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _file_bytes(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _filter_cell_value(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _flush_session_unlocked(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _gc_snapshots(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _has_grant(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _invalidate_segment(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _invalidate_sheet_indexes(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _is_member(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _is_solo(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _json(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _matches_filter(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _materialized_connection_unlocked(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _maybe_flush(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _mutate_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _normalize_cells(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _notify_comment_mentions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _open_session_connection(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _open_sqlite(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _open_sqlite_memory(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _promote_indexes(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _prune_revision_blobs(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _publish_revision(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _publish_workbook(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _purge_workbook_rows(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _read_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _read_sheets_from_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _read_viewport_metadata(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _read_viewport_solo(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _refresh_workbook_status(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _replay_unflushed(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _resolver_for(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _save_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _serialize_connection(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _shift_axis(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _shift_range_permissions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _validate_cells(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _validate_workbook_file(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _viewer_permissions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _workbook(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _write_file_revision(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _apply_structural_operation(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _apply_normalized_cell(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _append_op_durable(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
