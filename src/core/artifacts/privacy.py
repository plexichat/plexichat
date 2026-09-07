"""
Privacy / DSAR helpers for the artifacts module.

Implements the account-deletion / anonymization hook used by the account
reaper so that a user's artifact and transcript data is either scrubbed
(anonymized) or removed entirely, depending on the configured
``anonymize_content`` policy. This keeps the DSAR export (which includes the
user's own ``voice_calls`` and ``voice_call`` / ``transcript`` artifacts) in
sync with the erasure flow: once an account is purged, the user's identifiable
link to that content is gone.
"""

import json
from typing import Any, Dict, Optional

import utils.logger as logger

# Sentinel author id used when anonymizing content in place of the deleted user.
ANONYMIZED_AUTHOR_ID = 0


def _parse_json(text: Any) -> Any:
    if text is None or text == "":
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


_SCRUB_TEXT_KEYS = {
    "text",
    "content",
    "document",
    "content_html",
    "source",
    "board",
    "speaker",
}


def _scrub_node(node: Any) -> Any:
    """Recursively strip text-bearing keys and per-segment speaker labels."""
    if isinstance(node, dict):
        return {
            key: _scrub_node(value)
            for key, value in node.items()
            if key not in _SCRUB_TEXT_KEYS
        }
    if isinstance(node, list):
        return [_scrub_node(value) for value in node]
    return node


def _scrub_transcript_text(payload: Any) -> Optional[str]:
    """Return a scrubbed transcript payload string with PII text removed."""
    data = _parse_json(payload)
    if not isinstance(data, dict):
        return None
    return json.dumps(_scrub_node(data), ensure_ascii=False)


def _scrub_owned_artifact_row(db, row: Dict[str, Any]) -> bool:
    """Remove a user's identity from one owned artifact row, in place.

    Sets ``author_id`` to the sentinel anonymized id, blanks title/summary,
    and strips inline transcript/document text from the ``payload``. Shared
    code path for the anonymize policy and for hard erasure of content that
    must survive because other members co-own it (L22).
    """
    artifact_id = row["id"]
    new_payload: Optional[str] = None
    if row.get("artifact_type") in (
        "transcript",
        "plexiscribe",
        "plexiscript",
        "whiteboard",
        "voice_call",
    ):
        new_payload = _scrub_transcript_text(row.get("payload"))
    try:
        if new_payload is not None:
            db.execute(
                """
                UPDATE artifacts
                SET author_id = ?, title = '[deleted user content]',
                    summary = NULL, payload = ?
                WHERE id = ?
                """,
                (ANONYMIZED_AUTHOR_ID, new_payload, artifact_id),
            )
        else:
            db.execute(
                """UPDATE artifacts
                   SET author_id = ?, title = '[deleted user content]', summary = NULL
                   WHERE id = ?""",
                (ANONYMIZED_AUTHOR_ID, artifact_id),
            )
        return True
    except Exception as e:
        logger.error(f"Failed to anonymize artifact {artifact_id}: {e}")
        return False


def anonymize_user_artifacts(
    db,
    user_id: int,
    config: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Scrub or delete all artifact/transcript data owned by ``user_id``.

    Behavior is driven by ``config["anonymize_content"]`` (default ``True``),
    matching the policy already applied to ``msg_messages`` by the account
    reaper:

    * ``anonymize_content=True`` — keeps the rows but removes the user's
      identifiable link: ``author_id`` is set to a sentinel anonymized id,
      inline transcript/document text is stripped from the ``payload``,
      and the user is removed from any
      ``consented_participants`` lists on voice calls they only consented to
      (calls they initiated are anonymized rather than deleted).
    * ``anonymize_content=False`` — deletes artifacts authored by the user and
      any ``voice_calls`` rows they initiated; consented-participant entries
      are simply removed from calls they did not start. Content the user
      shared with a server (``server_id`` set) is co-owned by the server's
      other members, so it is scrubbed rather than deleted even in this hard
      mode (L22).

    Returns the number of rows touched (anonymized or deleted).
    """
    config = config or {}
    anonymize = config.get("anonymize_content", True)
    touched = 0

    try:
        owned_artifacts = db.fetch_all(
            """
            SELECT id, artifact_type, payload, server_id
            FROM artifacts WHERE author_id = ?
            """,
            (user_id,),
        )
    except Exception as e:
        logger.error(
            f"Failed to load artifacts for anonymization of user {user_id}: {e}"
        )
        owned_artifacts = []

    # Use the artifact manager's media/cascade helpers rather than raw deletes.
    # This keeps voice_calls, artifact_ops, and referenced media consistent
    # with ordinary artifact deletion.
    from src.core.artifacts.manager import ArtifactManager

    artifact_manager = ArtifactManager(db, config)

    for row in owned_artifacts:
        artifact_id = row["id"]
        # Anonymization deliberately keeps the artifact/media relationship
        # intact: the documented policy preserves rows while removing the
        # user's identity. Hard deletion owns media cleanup through the
        # manager's cascade path below.
        is_server_shared = row.get("server_id") is not None
        # L22: content shared with a server (SUM docs, shared voice
        # recordings, whiteboards, ...) is co-owned by the server's other
        # members. Even a hard erasure must not delete it out from under
        # them - scrub the deleted user's identity off the row instead.
        if anonymize or is_server_shared:
            if _scrub_owned_artifact_row(db, row):
                touched += 1
        else:
            try:
                owned = artifact_manager.get(artifact_id)
                if owned is not None:
                    artifact_manager._purge_artifact_media(owned)
            except Exception as e:
                logger.debug(
                    "Failed to purge media for artifact %s: %s", artifact_id, e
                )
            try:
                if artifact_manager.delete(artifact_id, purge_media=True):
                    touched += 1
            except Exception as e:
                logger.error(f"Failed to delete artifact {artifact_id}: {e}")

    # Strip the user from consented_participants on every voice call, and
    # handle calls they initiated.
    try:
        voice_calls = db.fetch_all(
            """
            SELECT id, artifact_id, initiator_id, consented_participants
            FROM voice_calls
            WHERE initiator_id = ? OR consented_participants IS NOT NULL
            """,
            (user_id,),
        )
        if voice_calls:
            voice_calls = [
                row
                for row in voice_calls
                if int(row.get("initiator_id") or 0) == user_id
                or user_id in (_parse_json(row.get("consented_participants")) or [])
            ]
    except Exception as e:
        logger.error(
            f"Failed to load voice calls for anonymization of user {user_id}: {e}"
        )
        voice_calls = []

    for row in voice_calls:
        call_id = row["id"]
        consented = _parse_json(row.get("consented_participants"))
        if not isinstance(consented, list):
            consented = []
        new_consented = [p for p in consented if p != user_id]
        try:
            if row.get("initiator_id") == user_id:
                if anonymize:
                    db.execute(
                        """
                        UPDATE voice_calls
                        SET initiator_id = ?, consented_participants = ?
                        WHERE id = ?
                        """,
                        (
                            ANONYMIZED_AUTHOR_ID,
                            json.dumps(new_consented),
                            call_id,
                        ),
                    )
                else:
                    # The linked artifact cascade owns the voice_calls row.
                    # If there is no linked artifact, remove the orphan row.
                    linked = row.get("artifact_id")
                    if linked:
                        # The owned-artifact pass may already have cascaded this
                        # voice_calls row. Only delete a still-existing linked
                        # artifact; otherwise remove an orphan call directly.
                        # L22: a linked artifact that survived hard erasure
                        # (server-shared content) must not be deleted here;
                        # drop this call record's initiator linkage instead.
                        linked_artifact = artifact_manager.get(int(linked))
                        if linked_artifact is None:
                            if db.fetch_one(
                                "SELECT id FROM voice_calls WHERE id = ?",
                                (call_id,),
                            ):
                                db.execute(
                                    "DELETE FROM voice_calls WHERE id = ?", (call_id,)
                                )
                        elif getattr(linked_artifact, "server_id", None) is not None:
                            db.execute(
                                """
                                UPDATE voice_calls
                                SET initiator_id = ?, consented_participants = ?
                                WHERE id = ?
                                """,
                                (
                                    ANONYMIZED_AUTHOR_ID,
                                    json.dumps(new_consented),
                                    call_id,
                                ),
                            )
                        else:
                            artifact_manager.delete(int(linked), purge_media=True)
                    else:
                        db.execute("DELETE FROM voice_calls WHERE id = ?", (call_id,))
                touched += 1
            elif new_consented != consented:
                db.execute(
                    "UPDATE voice_calls SET consented_participants = ? WHERE id = ?",
                    (json.dumps(new_consented), call_id),
                )
                touched += 1
        except Exception as e:
            logger.error(f"Failed to anonymize voice call {call_id}: {e}")

    # Remove the user as an actor from collaborative artifact ops so no
    # username/identity lingers in the operations log.
    try:
        result = db.execute("DELETE FROM artifact_ops WHERE actor_id = ?", (user_id,))
        if getattr(result, "rowcount", 0):
            touched += int(result.rowcount)
    except Exception as e:
        logger.error(f"Failed to scrub artifact_ops for user {user_id}: {e}")

    logger.info(
        f"Artifact anonymization for user {user_id}: {touched} rows "
        f"({'anonymized' if anonymize else 'deleted'})."
    )
    return touched
