"""
Artifacts repository - Data access for the `artifacts` and `voice_calls` tables.

These functions operate directly on a `db` connection (the same pattern used by
the messaging/voice managers) and translate between DB rows and the dataclass
models in `models.py`. All values are bound as query parameters; sort keys are
constrained to an explicit allow-list so no user input is interpolated into SQL.
"""

import json
import time
from typing import Any, Dict, List, Optional

from src.core.base import SnowflakeID
from .models import (
    Artifact,
    ArtifactType,
    ArtifactStatus,
    VoiceCall,
)


_COLUMNS = (
    "id",
    "conversation_id",
    "channel_id",
    "server_id",
    "author_id",
    "artifact_type",
    "title",
    "summary",
    "status",
    "recorded",
    "has_transcript",
    "payload",
    "retention_policy",
    "expires_at",
    "license_feature",
    "created_at",
    "updated_at",
)

# Allow-list of columns that may be used in ORDER BY. `duration` is a synthetic
# key resolved to the `voice_calls.duration_seconds` column via a LEFT JOIN.
_SORT_COLUMNS = {
    "created_at": "a.created_at",
    "title": "a.title",
    "type": "a.artifact_type",
    "duration": "vc.duration_seconds",
}


def _json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def row_to_artifact(row: Dict[str, Any]) -> Artifact:
    """Build an :class:`Artifact` from a DB row dict."""
    return Artifact(
        id=row["id"],
        conversation_id=row.get("conversation_id"),
        channel_id=row.get("channel_id"),
        server_id=row.get("server_id"),
        author_id=row["author_id"],
        artifact_type=ArtifactType(row["artifact_type"]),
        title=row["title"],
        summary=row.get("summary"),
        status=ArtifactStatus(row["status"]),
        recorded=bool(row.get("recorded", 0)),
        has_transcript=bool(row.get("has_transcript", 0)),
        payload=_json_loads(row.get("payload")) or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        retention_policy=_json_loads(row.get("retention_policy")),
        expires_at=row.get("expires_at"),
        license_feature=row.get("license_feature"),
    )


def artifact_to_row(artifact: Artifact) -> Dict[str, Any]:
    """Serialize an :class:`Artifact` back into a column→value dict."""
    return {
        "id": artifact.id,
        "conversation_id": artifact.conversation_id,
        "channel_id": artifact.channel_id,
        "server_id": artifact.server_id,
        "author_id": artifact.author_id,
        "artifact_type": artifact.artifact_type.value,
        "title": artifact.title,
        "summary": artifact.summary,
        "status": artifact.status.value,
        "recorded": 1 if artifact.recorded else 0,
        "has_transcript": 1 if artifact.has_transcript else 0,
        "payload": _json_dumps(artifact.payload),
        "retention_policy": _json_dumps(artifact.retention_policy),
        "expires_at": artifact.expires_at,
        "license_feature": artifact.license_feature,
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
    }


def create_artifact(db, artifact: Artifact) -> Artifact:
    """Insert an artifact row and return it unchanged."""
    row = artifact_to_row(artifact)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    column_list = ", ".join(_COLUMNS)
    query = f"INSERT INTO artifacts ({column_list}) VALUES ({placeholders})"
    db.execute(query, tuple(row[c] for c in _COLUMNS))
    return artifact


def get_artifact(db, artifact_id: SnowflakeID) -> Optional[Artifact]:
    """Fetch a single artifact by id."""
    row = db.fetch_one("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
    if not row:
        return None
    return row_to_artifact(row)


def update_artifact(db, artifact_id: SnowflakeID, **fields: Any) -> Optional[Artifact]:
    """Update the given columns for an artifact and return the refreshed row.

    Only known columns are accepted; unknown keys are ignored to avoid
    constructing SQL from arbitrary input.
    """
    allowed: Dict[str, Any] = {}
    model_fields = set(Artifact.__dataclass_fields__.keys())
    for key, value in fields.items():
        if key not in _COLUMNS:
            continue
        if key in model_fields and key in ("artifact_type",):
            allowed[key] = value.value if hasattr(value, "value") else value
        elif key in ("status",):
            allowed[key] = value.value if hasattr(value, "value") else value
        elif key in ("recorded", "has_transcript"):
            allowed[key] = 1 if value else 0
        elif key in ("payload", "retention_policy"):
            allowed[key] = _json_dumps(value)
        else:
            allowed[key] = value

    if not allowed:
        return get_artifact(db, artifact_id)

    set_clause = ", ".join(f"{col} = ?" for col in allowed)
    params = tuple(allowed.values()) + (artifact_id,)
    db.execute(
        f"UPDATE artifacts SET {set_clause} WHERE id = ?",
        params,
    )
    return get_artifact(db, artifact_id)


def delete_artifact(db, artifact_id: SnowflakeID) -> bool:
    """Delete an artifact row. Returns True if a row was removed."""
    cursor = db.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
    return bool(getattr(cursor, "rowcount", 0) > 0)


def _build_where(filters: Dict[str, Any]) -> tuple[str, List[Any]]:
    """Construct a parameterized WHERE clause from the filters dict."""
    clauses: List[str] = []
    params: List[Any] = []

    # Exact-match scalar filters.
    for col in (
        "conversation_id",
        "channel_id",
        "server_id",
        "author_id",
        "status",
    ):
        if filters.get(col) is not None:
            clauses.append(f"a.{col} = ?")
            value = filters[col]
            if col == "status" and hasattr(value, "value"):
                value = value.value
            params.append(value)

    # Artifact type: accepts a single value or a list.
    artifact_type = filters.get("artifact_type")
    if artifact_type is not None:
        if isinstance(artifact_type, (list, tuple, set)):
            items = [t.value if hasattr(t, "value") else t for t in artifact_type]
            placeholders = ", ".join("?" for _ in items)
            clauses.append(f"a.artifact_type IN ({placeholders})")
            params.extend(items)
        else:
            value = (
                artifact_type.value
                if hasattr(artifact_type, "value")
                else artifact_type
            )
            clauses.append("a.artifact_type = ?")
            params.append(value)

    # Boolean flags stored as ints.
    if filters.get("recorded") is not None:
        clauses.append("a.recorded = ?")
        params.append(1 if filters["recorded"] else 0)
    if filters.get("has_transcript") is not None:
        clauses.append("a.has_transcript = ?")
        params.append(1 if filters["has_transcript"] else 0)

    # Free-text search across title/summary.
    search = filters.get("search")
    if search:
        clauses.append("(a.title LIKE ? OR a.summary LIKE ?)")
        like = f"%{search}%"
        params.extend((like, like))

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_artifacts(db, filters: Optional[Dict[str, Any]] = None) -> List[Artifact]:
    """List artifacts matching ``filters`` with sorting and pagination."""
    filters = filters or {}
    where, params = _build_where(filters)

    sort_by = filters.get("sort_by", "created_at")
    sort_column = _SORT_COLUMNS.get(sort_by, "a.created_at")
    sort_order = (
        "DESC" if str(filters.get("sort_order", "desc")).lower() == "desc" else "ASC"
    )

    joins = ""
    if sort_by == "duration":
        joins = " LEFT JOIN voice_calls vc ON vc.artifact_id = a.id"

    limit = filters.get("limit")
    offset = filters.get("offset", 0)

    query = (
        f"SELECT a.* FROM artifacts a{joins}{where} ORDER BY {sort_column} {sort_order}"
    )
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend((int(limit), int(offset)))

    rows = db.fetch_all(query, tuple(params))
    return [row_to_artifact(row) for row in rows]


def count_artifacts(db, filters: Optional[Dict[str, Any]] = None) -> int:
    """Count artifacts matching ``filters`` (ignoring limit/offset/sort)."""
    filters = filters or {}
    where, params = _build_where(filters)
    row = db.fetch_one(
        f"SELECT COUNT(*) AS count FROM artifacts a{where}", tuple(params)
    )
    return int(row["count"]) if row else 0


# === Voice calls (voice_calls table) ===

_VOICE_CALL_COLUMNS = (
    "id",
    "artifact_id",
    "conversation_id",
    "channel_id",
    "server_id",
    "initiator_id",
    "started_at",
    "ended_at",
    "duration_seconds",
    "recorded",
    "transcript_artifact_id",
    "consented_participants",
    "participant_count",
    "created_at",
    "updated_at",
)


def row_to_voice_call(row: Dict[str, Any]) -> VoiceCall:
    """Build a :class:`VoiceCall` from a DB row dict."""
    consented = _json_loads(row.get("consented_participants"))
    if consented is None:
        consented = []
    return VoiceCall(
        id=row["id"],
        conversation_id=row.get("conversation_id"),
        channel_id=row.get("channel_id"),
        server_id=row.get("server_id"),
        initiator_id=row.get("initiator_id"),
        started_at=row["started_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        artifact_id=row.get("artifact_id"),
        ended_at=row.get("ended_at"),
        duration_seconds=row.get("duration_seconds"),
        recorded=bool(row.get("recorded", 0)),
        transcript_artifact_id=row.get("transcript_artifact_id"),
        consented_participants=list(consented),
        participant_count=int(row.get("participant_count", 0) or 0),
    )


def voice_call_to_row(call: VoiceCall) -> Dict[str, Any]:
    """Serialize a :class:`VoiceCall` back into a column→value dict."""
    return {
        "id": call.id,
        "artifact_id": call.artifact_id,
        "conversation_id": call.conversation_id,
        "channel_id": call.channel_id,
        "server_id": call.server_id,
        "initiator_id": call.initiator_id,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "duration_seconds": call.duration_seconds,
        "recorded": 1 if call.recorded else 0,
        "transcript_artifact_id": call.transcript_artifact_id,
        "consented_participants": _json_dumps(call.consented_participants),
        "participant_count": call.participant_count,
        "created_at": call.created_at,
        "updated_at": call.updated_at,
    }


def create_voice_call(db, call: VoiceCall) -> VoiceCall:
    """Insert a voice_calls row and return it unchanged."""
    row = voice_call_to_row(call)
    placeholders = ", ".join("?" for _ in _VOICE_CALL_COLUMNS)
    column_list = ", ".join(_VOICE_CALL_COLUMNS)
    query = f"INSERT INTO voice_calls ({column_list}) VALUES ({placeholders})"
    db.execute(query, tuple(row[c] for c in _VOICE_CALL_COLUMNS))
    return call


def get_voice_call(db, call_id: SnowflakeID) -> Optional[VoiceCall]:
    """Fetch a single voice call by id."""
    row = db.fetch_one("SELECT * FROM voice_calls WHERE id = ?", (call_id,))
    if not row:
        return None
    return row_to_voice_call(row)


def get_active_voice_call_by_channel(
    db, channel_id: SnowflakeID
) -> Optional[VoiceCall]:
    """Fetch the active (not ended) voice call for a channel, if any."""
    row = db.fetch_one(
        "SELECT * FROM voice_calls WHERE channel_id = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (channel_id,),
    )
    if not row:
        return None
    return row_to_voice_call(row)


# === Server artifact settings (per-server retention overrides) ===


def get_server_retention_days(db, server_id: SnowflakeID) -> Optional[int]:
    """Return the per-server retention override (days) or ``None``.

    Reads from the ``server_artifact_settings`` table created by migration 048.
    Returns ``None`` when no override exists or on any error (the caller then
    falls back to the global default).
    """
    try:
        row = db.fetch_one(
            "SELECT retention_days FROM server_artifact_settings WHERE server_id = ?",
            (server_id,),
        )
        if not row:
            return None
        raw = row.get("retention_days")
        if raw is None:
            return None
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return None
        return days if days > 0 else None
    except Exception:
        return None


def set_server_retention_days(
    db, server_id: SnowflakeID, retention_days: Optional[int]
) -> None:
    """Create or update a per-server retention override.

    Passing ``retention_days=None`` clears the override (deletes the row), so
    the server falls back to the global ``default_retention_days``.
    """
    now = int(time.time() * 1000)
    if retention_days is None:
        db.execute(
            "DELETE FROM server_artifact_settings WHERE server_id = ?",
            (server_id,),
        )
        return

    try:
        days = int(retention_days)
    except (TypeError, ValueError):
        raise ValueError("retention_days must be a positive integer or None")
    if days <= 0:
        raise ValueError("retention_days must be a positive integer or None")

    existing = db.fetch_one(
        "SELECT id FROM server_artifact_settings WHERE server_id = ?",
        (server_id,),
    )
    if existing:
        db.execute(
            "UPDATE server_artifact_settings "
            "SET retention_days = ?, updated_at = ? WHERE server_id = ?",
            (days, now, server_id),
        )
    else:
        db.execute(
            "INSERT INTO server_artifact_settings "
            "(server_id, retention_days, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (server_id, days, now, now),
        )


def update_voice_call(db, call_id: SnowflakeID, **fields: Any) -> Optional[VoiceCall]:
    """Update mutable columns of a voice call row."""
    allowed: Dict[str, Any] = {}
    for key, value in fields.items():
        if key not in _VOICE_CALL_COLUMNS:
            continue
        if key in ("recorded",):
            allowed[key] = 1 if value else 0
        elif key in ("consented_participants",):
            allowed[key] = _json_dumps(value)
        else:
            allowed[key] = value

    if not allowed:
        return get_voice_call(db, call_id)

    set_clause = ", ".join(f"{col} = ?" for col in allowed)
    params = tuple(allowed.values()) + (call_id,)
    db.execute(
        f"UPDATE voice_calls SET {set_clause} WHERE id = ?",
        params,
    )
    return get_voice_call(db, call_id)
