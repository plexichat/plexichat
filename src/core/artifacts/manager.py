"""
Artifacts manager - Business logic for the artifacts feature.

Wraps the repository (DB access) and the artifacts config to provide a clean
facade for creating, listing, retrieving, updating, and deleting artifacts, as
well as retroactively converting an existing upload/attachment into an artifact.

This group is intentionally self-contained: it contains no routes, no websocket
handlers, and no voice-specific call logic (those are introduced by later
groups). Permission/visibility checks are performed by the route layer.
"""

from typing import Any, Dict, List, Optional

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from .models import (
    Artifact,
    ArtifactType,
    ArtifactStatus,
)
from .repository import (
    create_artifact,
    get_artifact,
    update_artifact,
    delete_artifact,
    list_artifacts,
    count_artifacts,
)


class ArtifactManager(BaseManager):
    """Manager for artifact domain logic."""

    def __init__(self, db, config: Optional[Dict[str, Any]] = None) -> None:
        """
        Initialize the artifacts manager.

        Args:
            db: Database instance (must be connected).
            config: Optional artifacts config dict. When omitted the config is
                loaded via ``utils.config.get("artifacts", {})``.
        """
        # BaseManager expects an auth_module argument; we only need the db.
        super().__init__(db, None)
        self._artifacts_config = config if config is not None else {}

    # === Retention helpers ===

    def compute_expires_at(
        self,
        retention_days: Optional[int],
        created_at: int,
    ) -> Optional[int]:
        """Compute an expiry timestamp (ms) from a retention period in days.

        Returns ``None`` when ``retention_days`` is ``None`` (no expiry) or not
        a positive number, so callers can distinguish "never expires" from a
        concrete timestamp.
        """
        if retention_days is None:
            return None
        try:
            days = int(retention_days)
        except (TypeError, ValueError):
            return None
        if days <= 0:
            return None
        seconds_per_day = 86400
        return created_at + days * seconds_per_day * 1000

    def _resolve_retention_days(
        self,
        retention_policy: Any,
        server_id: Optional[SnowflakeID],
    ) -> Optional[int]:
        """Resolve the effective retention period (days) for a new artifact.

        Priority:
        1. An explicit per-artifact ``retention_policy`` carrying ``days``.
        2. A per-server override (when ``allow_per_server_override`` is set and
           the server config provides ``retention_days``).
        3. The global ``default_retention_days`` (``None`` => no expiry).
        """
        artifacts_cfg = self._artifacts_config or {}
        if not artifacts_cfg:
            artifacts_cfg = config.get("artifacts", {}) or {}

        days: Optional[int] = None

        if isinstance(retention_policy, dict):
            policy_days = retention_policy.get("days")
            if policy_days is not None:
                days = policy_days
        elif isinstance(retention_policy, (int, float, str)):
            try:
                days = int(retention_policy)
            except (TypeError, ValueError):
                days = None

        if days is None:
            allow_override = artifacts_cfg.get("allow_per_server_override", False)
            if allow_override and server_id is not None:
                db_override = self.get_server_retention_days(server_id)
                if db_override is not None:
                    days = db_override
                else:
                    servers = artifacts_cfg.get("servers", {}) or {}
                    server_cfg = servers.get(str(server_id)) or servers.get(server_id)
                    if (
                        isinstance(server_cfg, dict)
                        and server_cfg.get("retention_days") is not None
                    ):
                        days = server_cfg.get("retention_days")

        if days is None:
            days = artifacts_cfg.get("default_retention_days")

        return days

    # === Per-server retention override (server_artifact_settings) ===

    def get_server_retention_days(self, server_id: SnowflakeID) -> Optional[int]:
        """Return the per-server retention override for ``server_id``.

        Reads from the ``server_artifact_settings`` table (migration 048).
        Returns ``None`` when no override row exists, so callers fall back to
        the global ``default_retention_days``.
        """
        from .repository import get_server_retention_days as _get

        if self._db is None or server_id is None:
            return None
        return _get(self._db, server_id)

    def set_server_retention_days(
        self, server_id: SnowflakeID, retention_days: Optional[int]
    ) -> None:
        """Create, update, or clear the per-server retention override.

        ``retention_days=None`` clears the override so the server reverts to the
        global default.
        """
        from .repository import set_server_retention_days as _set

        if self._db is None or server_id is None:
            raise ValueError("db and server_id are required")
        _set(self._db, server_id, retention_days)
        # Existing artifacts that inherit retention (no explicit policy) must
        # follow the new server override immediately. Explicit per-artifact
        # policies remain authoritative.
        inherited = (
            self._db.fetch_all(
                "SELECT id, created_at FROM artifacts "
                "WHERE server_id = ? AND retention_policy IS NULL",
                (server_id,),
            )
            or []
        )
        effective_days = self._resolve_retention_days(None, server_id)
        for row in inherited:
            self._db.execute(
                "UPDATE artifacts SET expires_at = ?, updated_at = ? WHERE id = ?",
                (
                    self.compute_expires_at(effective_days, int(row["created_at"])),
                    self._get_timestamp(),
                    int(row["id"]),
                ),
            )

    # === CRUD ===

    def create(
        self,
        conversation_id: Optional[SnowflakeID],
        author_id: SnowflakeID,
        artifact_type: ArtifactType,
        title: str,
        summary: Optional[str] = None,
        channel_id: Optional[SnowflakeID] = None,
        server_id: Optional[SnowflakeID] = None,
        status: ArtifactStatus = ArtifactStatus.COMPLETED,
        recorded: bool = False,
        has_transcript: bool = False,
        payload: Optional[Dict[str, Any]] = None,
        retention_policy: Any = None,
        license_feature: Optional[str] = None,
    ) -> Artifact:
        """Create and persist a new artifact with a fresh Snowflake id."""
        created_at = self._get_timestamp()
        retention_days = self._resolve_retention_days(retention_policy, server_id)
        expires_at = self.compute_expires_at(retention_days, created_at)

        artifact = Artifact(
            id=self._generate_id(),
            conversation_id=conversation_id,
            channel_id=channel_id,
            server_id=server_id,
            author_id=author_id,
            artifact_type=artifact_type,
            title=title,
            summary=summary,
            status=status,
            recorded=recorded,
            has_transcript=has_transcript,
            payload=payload or {},
            created_at=created_at,
            updated_at=created_at,
            retention_policy=retention_policy,
            expires_at=expires_at,
            license_feature=license_feature,
        )
        created = create_artifact(self._db, artifact)
        self._emit_lifecycle_event("create", created)
        return created

    def get(self, artifact_id: SnowflakeID) -> Optional[Artifact]:
        """Fetch a single artifact by id."""
        return get_artifact(self._db, artifact_id)

    def update(
        self,
        artifact_id: SnowflakeID,
        **fields: Any,
    ) -> Optional[Artifact]:
        """Update an artifact's mutable fields and retention deadline."""
        existing = get_artifact(self._db, artifact_id)
        if existing is None:
            return None

        # Retention is derived data. Recompute it whenever the policy changes;
        # otherwise changing a policy leaves the old expiry in force forever.
        if "retention_policy" in fields:
            policy = fields["retention_policy"]
            if policy is None:
                fields["expires_at"] = None
            else:
                days = self._resolve_retention_days(policy, existing.server_id)
                fields["expires_at"] = self.compute_expires_at(
                    days, existing.created_at
                )

        fields.setdefault("updated_at", self._get_timestamp())
        updated = update_artifact(self._db, artifact_id, **fields)
        if updated is not None:
            self._emit_lifecycle_event("update", updated)
        return updated

    def delete(self, artifact_id: SnowflakeID, purge_media: bool = True) -> bool:
        """Delete an artifact row plus its cascade-linked rows.

        Removes the metadata row, the ordered operations log (``artifact_ops``),
        and linked ``voice_calls`` rows. When ``purge_media`` is set, any media
        files referenced by the artifact payload (e.g. voice-call recordings)
        are soft-deleted through the media module as a best-effort cleanup —
        media errors never fail the delete.

        Returns ``True`` when the metadata row was removed.
        """
        artifact = None
        try:
            artifact = get_artifact(self._db, artifact_id)
        except Exception:  # pragma: no cover - defensive
            artifact = None
        if artifact is not None and purge_media:
            self._purge_artifact_media(artifact)
        deleted = delete_artifact(self._db, artifact_id)
        if deleted and artifact is not None:
            self._emit_lifecycle_event("delete", artifact)
        return deleted

    def _emit_lifecycle_event(self, action: str, artifact: Artifact) -> None:
        """Best-effort dispatch of a typed artifact lifecycle event."""
        try:
            from src.core import events
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if not events.is_setup():
                return
            event_type = {
                "create": EventType.ARTIFACT_CREATE,
                "update": EventType.ARTIFACT_UPDATE,
                "delete": EventType.ARTIFACT_DELETE,
            }[action]
            event = Event(
                event_type=event_type,
                data={
                    "artifact_id": str(artifact.id),
                    "artifact_type": artifact.artifact_type.value,
                    "status": artifact.status.value,
                    "title": artifact.title,
                },
                server_id=artifact.server_id,
                channel_id=artifact.channel_id,
            )
            user_ids = None
            if artifact.conversation_id is not None:
                import src.api as api_mod

                messaging = api_mod.get_messaging()
                if messaging is not None:
                    user_ids = [
                        int(uid)
                        for uid in messaging.get_participant_ids(
                            artifact.conversation_id
                        )
                    ]
            events.dispatch(
                event,
                user_ids=user_ids,
                server_id=(int(artifact.server_id) if artifact.server_id else None),
                channel_id=(int(artifact.channel_id) if artifact.channel_id else None),
            )
        except Exception as exc:  # lifecycle events must not break persistence
            logger.debug("Artifact lifecycle event failed: %s", exc)

    # === Collaborative ops log ===

    def append_op(
        self,
        artifact_id: SnowflakeID,
        op_type: str,
        actor_id: Optional[int],
        data: Any,
    ) -> Optional[int]:
        """Persist a realtime op to the artifact's ops log.

        Returns the assigned ``seq`` or ``None`` when persistence was skipped
        (transient op type, missing artifact, or storage failure).
        """
        from .repository import append_artifact_op

        return append_artifact_op(self._db, artifact_id, op_type, actor_id, data)

    def list_ops(
        self,
        artifact_id: SnowflakeID,
        after_seq: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Return the artifact's persisted ops ordered by ``seq``.

        ``after_seq`` filters to ops strictly newer than that sequence, which
        lets a late joiner replay only what it has not yet seen.
        """
        from .repository import list_artifact_ops

        return list_artifact_ops(self._db, artifact_id, after_seq, limit)

    # === Media cleanup ===

    def _purge_artifact_media(self, artifact: Artifact) -> None:
        """Best-effort soft-delete of media files referenced by the payload."""
        payload = artifact.payload or {}
        if not isinstance(payload, dict):
            return
        file_ids: List[int] = []
        if payload.get("recording_file_id"):
            file_ids.append(payload["recording_file_id"])
        for fid in payload.get("recording_file_ids") or []:
            file_ids.append(fid)
        if not file_ids:
            return
        media_module = self._get_media_module()
        if media_module is None or not hasattr(media_module, "delete_file"):
            return
        author_id = int(artifact.author_id or 0)
        for fid in file_ids:
            try:
                media_module.delete_file(author_id, int(fid))
            except Exception as e:  # pragma: no cover - media is best-effort
                logger.debug(f"Media purge skipped for artifact {artifact.id}: {e}")

    @staticmethod
    def _get_media_module():
        try:
            import src.api as api_mod

            return api_mod.get_media()
        except Exception:  # pragma: no cover - defensive
            return None

    def list_with_filters(
        self,
        filters: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[SnowflakeID] = None,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        author_id: Optional[SnowflakeID] = None,
    ) -> List[Artifact]:
        """List artifacts with validated filters.

        Scope arguments (conversation/server/channel/author) supplied here are
        merged into the filters; the route layer remains responsible for
        enforcing that the caller is actually allowed to see those scopes.
        """
        merged = dict(filters or {})
        if conversation_id is not None:
            merged["conversation_id"] = conversation_id
        if server_id is not None:
            merged["server_id"] = server_id
        if channel_id is not None:
            merged["channel_id"] = channel_id
        if author_id is not None:
            merged["author_id"] = author_id
        return list_artifacts(self._db, merged)

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count artifacts matching the given filters."""
        return count_artifacts(self._db, filters or {})

    # === Upload conversion / retroactive artifact creation ===

    def convert_upload_to_artifact(
        self,
        attachment: Dict[str, Any],
        conversation_id: Optional[SnowflakeID],
        author_id: SnowflakeID,
        title: Optional[str] = None,
        artifact_type: ArtifactType = ArtifactType.UPLOAD,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Artifact:
        """Convert an existing upload/attachment into an artifact.

        The ``attachment`` dict carries the file metadata and must include at
        least an ``attachment_id`` (or ``id``) so the artifact can reference it.
        Optional keys ``filename``, ``content_type``, ``size``, and ``url`` are
        carried into the artifact ``payload``. This is the backend for the
        later "retroactive convert" client flow and intentionally does not
        couple to the media module.

        Returns the created artifact.

        Raises:
            ValueError: when the attachment size exceeds the configured
                ``max_artifact_size_mb``.
        """
        attachment_id = attachment.get("attachment_id") or attachment.get("id")
        if attachment_id is None:
            raise ValueError("attachment must include an 'attachment_id' or 'id'")

        max_size_mb = (self._artifacts_config or config.get("artifacts", {}) or {}).get(
            "max_artifact_size_mb", 200
        )
        max_size_bytes = max_size_mb * 1024 * 1024
        if attachment.get("size", 0) > max_size_bytes:
            raise ValueError(f"Attachment exceeds maximum size of {max_size_mb} MB")

        payload: Dict[str, Any] = {
            "attachment_id": attachment_id,
            "filename": attachment.get("filename"),
            "content_type": attachment.get("content_type"),
            "size": attachment.get("size"),
            "url": attachment.get("url"),
        }
        if isinstance(attachment.get("metadata"), dict):
            payload["metadata"] = attachment["metadata"]

        display_title = title or attachment.get("filename") or f"Upload {attachment_id}"

        return self.create(
            conversation_id=conversation_id,
            author_id=author_id,
            artifact_type=artifact_type,
            title=display_title,
            summary=f"Converted from attachment {attachment_id}",
            channel_id=channel_id,
            server_id=server_id,
            recorded=False,
            has_transcript=False,
            payload=payload,
        )

    def get_feature_settings(
        self, server_id: SnowflakeID, feature: str
    ) -> Dict[str, str]:
        """Get all per-server setting overrides for a feature."""
        from .repository import get_feature_settings as _get

        if self._db is None or server_id is None:
            return {}
        return _get(self._db, server_id, feature)

    def set_feature_setting(
        self, server_id: SnowflakeID, feature: str, key: str, value: Optional[str]
    ) -> None:
        """Set or clear a single feature setting override for a server."""
        from .repository import set_feature_setting as _set

        if self._db is None or server_id is None:
            raise ValueError("db and server_id are required")
        _set(self._db, server_id, feature, key, value)

    def set_feature_settings_bulk(
        self, server_id: SnowflakeID, feature: str, settings: Dict[str, Optional[str]]
    ) -> Dict[str, str]:
        """Set multiple feature settings and return the effective result."""
        from .repository import set_feature_settings_bulk as _bulk

        if self._db is None or server_id is None:
            raise ValueError("db and server_id are required")
        return _bulk(self._db, server_id, feature, settings)
