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

import utils.logger as logger

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
    "rev",
    "created_at",
    "updated_at",
)

# Repository-level default page size applied when a caller omits ``limit``
# (L7): prevents unbounded full-table materialization by internal callers.
_DEFAULT_LIST_LIMIT = 200


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


def _parse_artifact_type(row: Dict[str, Any]) -> ArtifactType:
    """Parse a persisted artifact_type string, tolerating legacy drift.

    ``ArtifactType`` raises for unknown non-null strings (L18: silently
    collapsing them to ``FUTURE`` masked schema drift and made divergent
    rows unfilterable). A read of such a row must not crash the caller, but
    the divergence must not pass silently either - log it and fall back to
    the explicit ``FUTURE`` member.
    """
    raw_type = row["artifact_type"]
    try:
        return ArtifactType(raw_type)
    except ValueError:
        logger.error(
            "artifact %s has unknown artifact_type %r; treating as 'future'",
            row["id"],
            raw_type,
        )
        return ArtifactType.FUTURE


def row_to_artifact(row: Dict[str, Any]) -> Artifact:
    """Build an :class:`Artifact` from a DB row dict."""
    return Artifact(
        id=row["id"],
        conversation_id=row.get("conversation_id"),
        channel_id=row.get("channel_id"),
        server_id=row.get("server_id"),
        author_id=row["author_id"],
        artifact_type=_parse_artifact_type(row),
        title=row["title"],
        summary=row.get("summary"),
        status=ArtifactStatus(row["status"]),
        recorded=bool(row.get("recorded", 0)),
        has_transcript=bool(row.get("has_transcript", 0)),
        payload=_json_loads(row.get("payload")) or {},
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        rev=int(row.get("rev", 0) or 0),
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
        "rev": int(artifact.rev or 0),
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
    }


def create_artifact(db, artifact: Artifact) -> Artifact:
    """Insert an artifact row and return it unchanged.

    A new artifact whose payload carries a legacy numeric ``rev`` (editor
    stamps) seeds the column from it so the first optimistic write continues
    the chain instead of colliding at rev 1.
    """
    row = artifact_to_row(artifact)
    if int(row["rev"] or 0) <= 0:
        from .revision import payload_revision

        seeded = payload_revision(artifact.payload)
        if seeded is not None and seeded > 0:
            row["rev"] = seeded
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


# Columns that may never be rewritten directly through ``update_artifact``:
# ``rev`` is the optimistic-concurrency revision and only moves via the
# atomic ``expected_rev`` path (or is seeded at insert). A stray ``rev`` in
# fields would let a caller rewind/forge the revision chain.
_IMMUTABLE_UPDATE_COLUMNS = frozenset({"rev"})


def update_artifact(
    db,
    artifact_id: SnowflakeID,
    expected_rev: Optional[int] = None,
    **fields: Any,
) -> Optional[Artifact]:
    """Update the given columns for an artifact and return the refreshed row.

    Only known columns are accepted; unknown keys are ignored to avoid
    constructing SQL from arbitrary input. ``rev`` is never writable through
    ``fields``; it is only advanced by the optimistic-concurrency path.

    Args:
        db: Database connection.
        artifact_id: The ID of the artifact to update.
        expected_rev: When provided, the row is updated only if its current
            ``rev`` equals this value, and ``rev`` is advanced by one in the
            same statement (``SET rev = rev + 1 ... WHERE id = ? AND rev = ?``).
            A concurrent writer who already advanced the revision makes the
            UPDATE match zero rows and this returns ``None`` — the caller can
            distinguish a stale write from a missing artifact by re-fetching.
        **fields: Column-value pairs to update. Only keys listed in
            ``_COLUMNS`` are accepted; unknown keys are silently ignored.

    Returns:
        The updated :class:`Artifact`, ``None`` when no artifact exists with
        the given ``artifact_id``, or (with ``expected_rev``) ``None`` when
        the row exists but its revision has already moved past ``expected_rev``.
    """
    allowed: Dict[str, Any] = {}
    model_fields = set(Artifact.__dataclass_fields__.keys())
    for key, value in fields.items():
        if key not in _COLUMNS or key in _IMMUTABLE_UPDATE_COLUMNS:
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

    if expected_rev is not None:
        set_clause = ", ".join(f"{col} = ?" for col in allowed)
        # ``rev = rev + 1`` is a raw expression: it must NOT be bound as a
        # parameter (SQLite binds literals, and ``rev = ? + 1`` with the old
        # value would not be atomic under a concurrent writer).
        params = tuple(allowed.values()) + (artifact_id, expected_rev)
        cursor = db.execute(
            f"UPDATE artifacts SET {set_clause}, rev = rev + 1 WHERE id = ? AND rev = ?",
            params,
        )
        if getattr(cursor, "rowcount", 0) <= 0:
            return None
        return get_artifact(db, artifact_id)

    set_clause = ", ".join(f"{col} = ?" for col in allowed)
    params = tuple(allowed.values()) + (artifact_id,)
    db.execute(
        f"UPDATE artifacts SET {set_clause} WHERE id = ?",
        params,
    )
    return get_artifact(db, artifact_id)


def delete_artifact_cascade(db, artifact_id: SnowflakeID) -> bool:
    """Delete an artifact row and all rows that reference it.

    Removes the artifact's metadata row plus its ordered operations log
    (``artifact_ops``) and any voice-call records linked via
    ``voice_calls.artifact_id``. Media purges are the caller's responsibility
    (the repository deliberately does not couple to the media module).

    Args:
        db: Database connection.
        artifact_id: The ID of the artifact to delete.

    Returns:
        ``True`` if the metadata row was removed, ``False`` otherwise.
    """
    try:
        db.execute("DELETE FROM artifact_ops WHERE artifact_id = ?", (artifact_id,))
        db.execute("DELETE FROM voice_calls WHERE artifact_id = ?", (artifact_id,))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Cascade cleanup failed for artifact {artifact_id}: {e}")
    cursor = db.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
    return bool(getattr(cursor, "rowcount", 0) > 0)


def delete_artifact(db, artifact_id: SnowflakeID) -> bool:
    """Delete an artifact row and its cascade-linked rows.

    Args:
        db: Database connection.
        artifact_id: The ID of the artifact to delete.

    Returns:
        ``True`` if a row was removed, ``False`` otherwise.
    """
    return delete_artifact_cascade(db, artifact_id)


def _build_where(filters: Dict[str, Any]) -> tuple[str, List[Any]]:
    """Construct a parameterized WHERE clause from the filters dict."""
    clauses: List[str] = []
    params: List[Any] = []

    # Exact-match scalar filters.
    for col in (
        "conversation_id",
        "channel_id",
        "author_id",
        "status",
    ):
        if filters.get(col) is not None:
            clauses.append(f"a.{col} = ?")
            value = filters[col]
            if col == "status" and hasattr(value, "value"):
                value = value.value
            params.append(value)

    # Server id: accepts a single value or a list (used by the Suite shared
    # feed to gather the caller's memberships in one query).
    server_id = filters.get("server_id")
    if server_id is not None:
        if isinstance(server_id, (list, tuple, set, frozenset)):
            ids = [int(v) for v in server_id]
            if not ids:
                # ``IN ()`` is invalid SQL; an empty membership set must
                # match nothing rather than raise a syntax error.
                clauses.append("1 = 0")
            else:
                placeholders = ", ".join("?" for _ in ids)
                clauses.append(f"a.server_id IN ({placeholders})")
                params.extend(ids)
        else:
            clauses.append("a.server_id = ?")
            params.append(server_id)

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

    # Free-text search across title/summary. LIKE wildcards in user input are
    # escaped so a bare "%" cannot match every row (filter evasion / DoS on
    # listing); the escape char is declared explicitly in the clause.
    search = filters.get("search")
    if search:
        clauses.append("(a.title LIKE ? ESCAPE '\\' OR a.summary LIKE ? ESCAPE '\\')")
        like = "%{}%".format(
            str(search).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
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

    # L7: an omitted limit used to return the ENTIRE table (only the HTTP
    # route clamped). Cap the repository default so internal callers can never
    # accidentally materialize unbounded result sets; callers that need a
    # larger page pass an explicit limit.
    if limit is None:
        limit = _DEFAULT_LIST_LIMIT

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


# === Artifact ops log (artifact_ops table) ===

# Op types that carry no durable document state and are never persisted.
# ``presence`` / ``presence_leave`` announce board participants (the roster is
# ephemeral by design, like live cursors) and must never bloat the ops log.
_TRANSIENT_OP_TYPES = {
    "cursor",
    "snapshot_request",
    "typing",
    "selection",
    "presence",
    "presence_leave",
}
_MAX_ARTIFACT_OP_BYTES = 256 * 1024


def _fetch_next_seq(db, artifact_id: SnowflakeID) -> int:
    """Return the next per-artifact sequence number (MAX(seq) + 1).

    Uses ``db.execute`` directly (not ``fetch_one``) so the result is never
    served from the query cache: a just-inserted op must be visible.
    """
    row = db.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
        "FROM artifact_ops WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    if not row:
        return 1
    try:
        return int(row["next_seq"])
    except (KeyError, TypeError, IndexError):
        try:
            return int(row[0])
        except (TypeError, IndexError):
            return 1


def append_artifact_op(
    db,
    artifact_id: SnowflakeID,
    op_type: str,
    actor_id: Optional[int],
    data: Any,
) -> Optional[int]:
    """Append an op to an artifact's ordered operations log.

    Returns the assigned ``seq``, or ``None`` when the artifact does not exist
    or the op could not be persisted. ``seq`` is allocated as ``MAX(seq) + 1``
    and the ``UNIQUE(artifact_id, seq)`` constraint is used to detect races:
    on a collision the insert is retried with a fresh sequence number.

    Args:
        db: Database connection.
        artifact_id: Target artifact.
        op_type: Op kind (e.g. ``edit``, ``stroke``).
        actor_id: User that produced the op (nullable).
        data: JSON-serializable op payload.
    """
    if op_type in _TRANSIENT_OP_TYPES:
        return None
    try:
        if (
            len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            > _MAX_ARTIFACT_OP_BYTES
        ):
            logger.warning(
                "Artifact op rejected for %s: payload too large", artifact_id
            )
            return None
    except (TypeError, ValueError):
        logger.warning(
            "Artifact op rejected for %s: payload is not JSON serializable", artifact_id
        )
        return None
    if get_artifact(db, artifact_id) is None:
        return None
    now = int(time.time() * 1000)
    data_json = _json_dumps(data)
    for _ in range(8):
        seq = _fetch_next_seq(db, artifact_id)
        try:
            cursor = db.execute(
                "INSERT INTO artifact_ops "
                "(artifact_id, seq, op_type, actor_id, data, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, seq, op_type, actor_id, data_json, now),
            )
        except Exception as e:
            # Retry only on unique/integrity collisions; anything else surfaces.
            lowered = str(e).lower()
            if "unique" in lowered or "integrity" in lowered:
                continue
            logger.warning(f"Failed to persist artifact op for {artifact_id}: {e}")
            return None
        if getattr(cursor, "rowcount", 0):
            return seq
    logger.warning(f"Failed to persist artifact op for {artifact_id}: seq contention")
    return None


def list_artifact_ops(
    db,
    artifact_id: SnowflakeID,
    after_seq: int = 0,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Return ops for an artifact ordered by ``seq`` ascending.

    Each entry carries ``seq``, ``op_type``, ``actor_id``, ``created_at``, and
    the original op payload under ``op`` (the same shape used on the
    ``ARTIFACT_SYNC`` wire format for late-joiner replay).
    """
    safe_limit = max(1, min(int(limit), 2000))
    safe_after = max(0, int(after_seq))
    rows = db.fetch_all(
        "SELECT seq, op_type, actor_id, data, created_at "
        "FROM artifact_ops WHERE artifact_id = ? AND seq > ? "
        "ORDER BY seq ASC LIMIT ?",
        (artifact_id, safe_after, safe_limit),
    )
    ops: List[Dict[str, Any]] = []
    for row in rows:
        try:
            ops.append(
                {
                    "seq": int(row["seq"]),
                    "op_type": row["op_type"],
                    "actor_id": row.get("actor_id"),
                    "created_at": row.get("created_at"),
                    "op": _json_loads(row.get("data")),
                }
            )
        except Exception:  # pragma: no cover - defensive
            continue
    return ops


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
    Returns ``None`` when no override exists (the caller then falls back to the
    global default).
    """
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

    # Conditional upsert: UPDATE first (a no-op when the row is missing), then
    # INSERT only when nothing was updated. A concurrent insert can still hit
    # the UNIQUE(server_id) constraint; that means the other writer won the
    # race, so re-apply the UPDATE instead of surfacing a 500.
    cursor = db.execute(
        "UPDATE server_artifact_settings "
        "SET retention_days = ?, updated_at = ? WHERE server_id = ?",
        (days, now, server_id),
    )
    if getattr(cursor, "rowcount", 0) > 0:
        return
    try:
        db.execute(
            "INSERT INTO server_artifact_settings "
            "(server_id, retention_days, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (server_id, days, now, now),
        )
    except Exception as e:
        if not ("unique" in str(e).lower() or "integrity" in str(e).lower()):
            raise
        db.execute(
            "UPDATE server_artifact_settings "
            "SET retention_days = ?, updated_at = ? WHERE server_id = ?",
            (days, now, server_id),
        )


# Columns that may never be rewritten through ``update_voice_call``: they are
# set at insert time (identity, creation) or owned by other subsystems (the
# linked artifact id). Silently allowing them let a caller corrupt referential
# integrity (L8).
_VOICE_CALL_IMMUTABLE_COLUMNS = frozenset(
    {"id", "artifact_id", "created_at", "updated_at"}
)


def update_voice_call(db, call_id: SnowflakeID, **fields: Any) -> Optional[VoiceCall]:
    """Update mutable columns of a voice call row.

    Identity/creation columns (``id``, ``artifact_id``, ``created_at``,
    ``updated_at``) are always ignored, no matter what the caller passes, so
    a stray ``created_at`` cannot silently rewrite history (L8).
    """
    allowed: Dict[str, Any] = {}
    for key, value in fields.items():
        if key not in _VOICE_CALL_COLUMNS:
            continue
        if key in _VOICE_CALL_IMMUTABLE_COLUMNS:
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


# === Per-feature server settings (server_artifact_feature_settings table) ===


def get_feature_settings(db, server_id: SnowflakeID, feature: str) -> Dict[str, str]:
    """Return all setting key-value pairs for a feature on a server."""
    rows = db.fetch_all(
        "SELECT setting_key, setting_value FROM server_artifact_feature_settings "
        "WHERE server_id = ? AND feature = ?",
        (server_id, feature),
    )
    return {row["setting_key"]: row["setting_value"] for row in rows} if rows else {}


def set_feature_setting(
    db, server_id: SnowflakeID, feature: str, key: str, value: Optional[str]
) -> None:
    """Set or clear a single feature setting for a server.

    Pass value=None to delete the setting (revert to default).
    """
    now = int(time.time() * 1000)
    if value is None:
        db.execute(
            "DELETE FROM server_artifact_feature_settings "
            "WHERE server_id = ? AND feature = ? AND setting_key = ?",
            (server_id, feature, key),
        )
        return

    # Conditional upsert (see set_server_retention_days): avoids the
    # SELECT-then-INSERT race where two concurrent writers both miss the
    # SELECT and one INSERT dies on UNIQUE(server_id, feature, setting_key).
    cursor = db.execute(
        "UPDATE server_artifact_feature_settings SET setting_value = ?, updated_at = ? "
        "WHERE server_id = ? AND feature = ? AND setting_key = ?",
        (value, now, server_id, feature, key),
    )
    if getattr(cursor, "rowcount", 0) > 0:
        return
    try:
        db.execute(
            "INSERT INTO server_artifact_feature_settings "
            "(server_id, feature, setting_key, setting_value, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (server_id, feature, key, value, now, now),
        )
    except Exception as e:
        if not ("unique" in str(e).lower() or "integrity" in str(e).lower()):
            raise
        db.execute(
            "UPDATE server_artifact_feature_settings SET setting_value = ?, updated_at = ? "
            "WHERE server_id = ? AND feature = ? AND setting_key = ?",
            (value, now, server_id, feature, key),
        )


def set_feature_settings_bulk(
    db, server_id: SnowflakeID, feature: str, settings: Dict[str, Optional[str]]
) -> Dict[str, str]:
    """Set or clear multiple feature settings atomically.

    Returns the full effective settings after the operation.
    """
    in_trans = getattr(db, "in_transaction", False)
    if not in_trans:
        db.begin_transaction()
    try:
        for key, value in settings.items():
            set_feature_setting(db, server_id, feature, key, value)
        if not in_trans:
            db.commit()
    except Exception:
        if not in_trans:
            db.rollback()
        raise
    return get_feature_settings(db, server_id, feature)


def get_voice_call_uncached(db, call_id: SnowflakeID) -> Optional[VoiceCall]:
    """Fetch a voice call bypassing the query cache (fresh consent state)."""
    cursor = db.execute("SELECT * FROM voice_calls WHERE id = ?", (call_id,))
    row = cursor.fetchone()
    if not row:
        return None
    try:
        return row_to_voice_call(dict(row))
    except Exception:
        return None


def get_channel_voice_user_ids(db, channel_id: SnowflakeID) -> List[int]:
    """Return live ``voice_states`` member ids for a channel, bypassing cache."""
    cursor = db.execute(
        "SELECT user_id FROM voice_states WHERE channel_id = ?",
        (channel_id,),
    )
    rows = cursor.fetchall() or []
    user_ids: List[int] = []
    for row in rows:
        value = None
        try:
            value = row["user_id"]
        except (KeyError, TypeError, IndexError):
            try:
                value = row[0]
            except (TypeError, IndexError):
                value = None
        if value is None:
            continue
        try:
            user_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return user_ids
