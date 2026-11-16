"""DSAR export privacy guards.

Every DSAR export is a compliance artifact. Before any bytes are written to
storage the collected payload must satisfy a small set of invariants:

* it is stamped with the requesting data subject (never somebody else);
* it contains no rows owned solely by a different data subject;
* third-party personal data is redacted unless disclosure is legally required;
* it stays within configured size/record quotas and is complete.

The scope verification in this module is the first line of defence: a
collector that accidentally drops its ``WHERE user_id = ?`` predicate returns
rows belonging to other people. Those rows are detected here, before they can
be serialized into an export that is handed to the wrong user.
"""

from __future__ import annotations

import json
from typing import Any, Dict, FrozenSet, Iterable, Iterator

# Columns that identify the data subject a row belongs to. A row is
# considered in-scope when at least one of these columns carries the
# requesting user's id. Relationship pointers to *other* users (friend_id,
# blocked_id, reported_user_id, inviter_id, ...) are deliberately excluded:
# they are legitimate references recorded in the subject's own data, not
# ownership of a foreign row.
SUBJECT_ID_FIELDS: FrozenSet[str] = frozenset(
    {
        "user_id",
        "author_id",
        "owner_id",
        "editor_id",
        "blocker_id",
        "pinned_by",
        "original_author_id",
        "initiator_id",
        "actor_id",
        "created_by",
        "subject_id",
        "recipient_id",
        "reporter_id",
        "reported_user_id",
        "sender_id",
    }
)


class ExportScopeViolation(ValueError):
    """Raised when an export would contain another data subject's data."""


def _candidate_subject_ids(value: Any) -> Iterable[int]:
    """Yield integer ids from a field, tolerating string-coded ids."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield value
    elif isinstance(value, str) and value.isdigit():
        yield int(value)


def _iter_mappings(value: Any) -> Iterator[Dict[str, Any]]:
    """Yield every mapping in a (possibly nested) JSON-like structure."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_mappings(child)


def verify_export_scope(data: Dict[str, Any], user_id: int) -> None:
    """Fail closed when ``data`` contains another data subject's records.

    Checks that the envelope is stamped with the requesting subject and that
    every record carrying one of :data:`SUBJECT_ID_FIELDS` references the
    subject at least once. A collector that drops its ownership predicate
    returns rows attributed only to somebody else; those rows are rejected
    here instead of being shipped in a GDPR export.

    Raises:
        ExportScopeViolation: if the envelope identity is wrong or a row is
            owned exclusively by other users.
    """
    if data.get("user_id") != user_id:
        raise ExportScopeViolation(
            f"export envelope user_id={data.get('user_id')!r} != {user_id}"
        )

    identity = data.get("identity")
    if isinstance(identity, dict) and identity.get("id") not in (None, user_id):
        raise ExportScopeViolation(
            f"export identity row id={identity.get('id')!r} != {user_id}"
        )

    for record in _iter_mappings(data):
        owner_fields = {
            field: record[field] for field in SUBJECT_ID_FIELDS if field in record
        }
        owners = [
            candidate
            for value in owner_fields.values()
            for candidate in _candidate_subject_ids(value)
        ]
        if not owners:
            continue
        if user_id not in owners:
            evidence = json.dumps(owner_fields, sort_keys=True, default=str)
            raise ExportScopeViolation(
                f"export contains a row owned by another data subject: {evidence}"
            )


__all__ = ["ExportScopeViolation", "SUBJECT_ID_FIELDS", "verify_export_scope"]
