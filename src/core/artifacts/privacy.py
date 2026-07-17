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

# JSON key under which the flattened transcript text is surfaced in exports.
_TRANSCRIPT_TEXT_KEY = "transcript_text"


def _parse_json(text: Any) -> Any:
    if text is None or text == "":
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _scrub_transcript_text(payload: Any) -> Optional[str]:
    """Return a scrubbed transcript payload string with PII text removed."""
    data = _parse_json(payload)
    if not isinstance(data, dict):
        return None
    data.pop("text", None)
    segments = data.get("segments")
    if isinstance(segments, list):
        for seg in segments:
            if isinstance(seg, dict):
                seg.pop("text", None)
                seg.pop("speaker", None)
    return json.dumps(data, ensure_ascii=False)


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
      inline transcript text is stripped from the ``payload`` and the export
      ``transcript_text`` field, and the user is removed from any
      ``consented_participants`` lists on voice calls they only consented to
      (calls they initiated are anonymized rather than deleted).
    * ``anonymize_content=False`` — deletes artifacts authored by the user and
      any ``voice_calls`` rows they initiated; consented-participant entries
      are simply removed from calls they did not start.

    Returns the number of rows touched (anonymized or deleted).
    """
    config = config or {}
    anonymize = config.get("anonymize_content", True)
    touched = 0

    try:
        owned_artifacts = db.fetch_all(
            """
            SELECT id, artifact_type, payload
            FROM artifacts WHERE author_id = ?
            """,
            (user_id,),
        )
    except Exception as e:
        logger.error(
            f"Failed to load artifacts for anonymization of user {user_id}: {e}"
        )
        owned_artifacts = []

    for row in owned_artifacts:
        artifact_id = row["id"]
        if anonymize:
            new_payload: Optional[str] = None
            if row.get("artifact_type") == "transcript":
                new_payload = _scrub_transcript_text(row.get("payload"))
            try:
                if new_payload is not None:
                    db.execute(
                        """
                        UPDATE artifacts
                        SET author_id = ?, payload = ?
                        WHERE id = ?
                        """,
                        (ANONYMIZED_AUTHOR_ID, new_payload, artifact_id),
                    )
                else:
                    db.execute(
                        "UPDATE artifacts SET author_id = ? WHERE id = ?",
                        (ANONYMIZED_AUTHOR_ID, artifact_id),
                    )
                touched += 1
            except Exception as e:
                logger.error(f"Failed to anonymize artifact {artifact_id}: {e}")
        else:
            try:
                db.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
                db.execute(
                    "DELETE FROM artifact_ops WHERE artifact_id = ?",
                    (artifact_id,),
                )
                touched += 1
            except Exception as e:
                logger.error(f"Failed to delete artifact {artifact_id}: {e}")

    # Strip the user from consented_participants on every voice call, and
    # handle calls they initiated.
    try:
        voice_calls = db.fetch_all(
            """
            SELECT id, initiator_id, consented_participants
            FROM voice_calls
            WHERE initiator_id = ? OR consented_participants LIKE ?
            """,
            (user_id, f"%{user_id}%"),
        )
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
