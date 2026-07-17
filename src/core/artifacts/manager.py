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
        return create_artifact(self._db, artifact)

    def get(self, artifact_id: SnowflakeID) -> Optional[Artifact]:
        """Fetch a single artifact by id."""
        return get_artifact(self._db, artifact_id)

    def update(
        self,
        artifact_id: SnowflakeID,
        **fields: Any,
    ) -> Optional[Artifact]:
        """Update an artifact's mutable fields."""
        if "payload" in fields and fields["payload"] is not None:
            fields.setdefault("updated_at", self._get_timestamp())
        return update_artifact(self._db, artifact_id, **fields)

    def delete(self, artifact_id: SnowflakeID) -> bool:
        """Delete an artifact row.

        NOTE: this only removes the metadata row. Actual media purge (for
        uploads/files) and cascade cleanup of linked ``voice_calls`` /
        ``artifact_ops`` rows are handled by later groups; callers should purge
        the referenced media after a successful delete.
        """
        return delete_artifact(self._db, artifact_id)

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
        """
        attachment_id = attachment.get("attachment_id") or attachment.get("id")
        if attachment_id is None:
            raise ValueError("attachment must include an 'attachment_id' or 'id'")

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
