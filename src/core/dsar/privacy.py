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
from typing import Any, Dict, FrozenSet, Iterable, Iterator, Set, Tuple

# Placeholder written wherever a third party's personal data was removed.
REDACTED = "[redacted]"


# Internal-only column names that have no business in an artifact handed to a
# data subject. Several collectors project ``SELECT *`` into the export
# (applications, suite, ...): that copied secret/token hashes and server-side
# storage paths straight into the JSON/ZIP. The per-collector redaction is
# patchy — e.g. ApplicationsCollector deletes ``bot_token_encrypted`` and
# ``public_key_encrypted``, columns that do not exist in the live schema, while
# the real ``client_secret_hash`` was exported — so this denylist is the final,
# fail-closed gate applied to the whole payload at the single choke point every
# collector passes through (DataCollector.collect_all). A future collector that
# projects a secret/path by accident cannot leak it past here.
INTERNAL_EXPORT_FIELDS: FrozenSet[str] = frozenset(
    {
        # Authentication / secret material (password-hash class).
        "password_hash",
        "totp_secret_encrypted",
        "backup_codes_hash",
        "client_secret_hash",
        "bot_token_encrypted",
        "public_key_encrypted",
        "token_hash",
        "access_token_hash",
        "refresh_token_hash",
        "code_hash",
        "ticket_hash",
        "token_encrypted",
        # Server filesystem / object-storage layout (internal paths).
        "storage_path",
        "blob_path",
        # Deterministic ciphertext that collectors redact in some tables but
        # not others (e.g. auth_known_ips); never ship raw ciphertext.
        "ip_encrypted",
        "ua_encrypted",
        "email_encrypted",
        "external_id_encrypted",
        "old_content_encrypted",
        "url_encrypted",
        # Residual ciphertext reachable through ``SELECT *`` collectors that
        # the original denylist missed: notification previews are encrypted
        # with a deterministic KEK, and poll questions may be stored ONLY as
        # ciphertext (the plaintext column is blank when at-rest encryption
        # is enabled). Both were shipped raw in JSON and ZIP artifacts.
        "content_preview_encrypted",
        "question_encrypted",
        "text_encrypted",
        "option_encrypted",
        "content_encrypted",
        # Reviewer/admin-only notes collected verbatim by the ``SELECT *``
        # feedback and reports collectors. Their encrypted twin
        # (``internal_notes_encrypted``) is dropped by the ``_encrypted``
        # suffix below, but the plaintext fields carry no secret suffix and
        # survived the gate, shipping internal reviewer commentary to the
        # data subject. They are internal-only by definition and have no
        # place in a portability artifact.
        "internal_notes",
        "internal_notes_format",
        "admin_notes",
        # ``user_features.notes`` is documented as "Admin notes" and written
        # only by the admin feature-management path, but the ``SELECT *``
        # FeaturesCollector ships it verbatim. Its bare name carries no secret
        # suffix, so it survived the gate just like ``admin_notes`` did before
        # N771. Deny the generic "notes" column too: in this schema it is the
        # admin-only feature note and no subject-owned table projects a field
        # of that name, so a subject's legitimate data is never dropped.
        "notes",
    }
)

# Fail-closed suffix rule: a future collector that projects a new
# secret/ciphertext column via ``SELECT *`` must not leak it just because
# nobody remembered to add the literal name to the denylist above. These
# suffixes only ever mark internal secret/ciphertext material in this schema;
# ordinary metadata (checksums, indexes, fingerprints, ids) is unaffected.
INTERNAL_EXPORT_FIELD_SUFFIXES: Tuple[str, ...] = (
    "_encrypted",
    "_hash",
    "_salt",
    "_nonce",
)


def _is_internal_export_field(key: Any) -> bool:
    """Whether a mapping key names an internal-only secret/path field."""
    return key in INTERNAL_EXPORT_FIELDS or (
        isinstance(key, str) and key.endswith(INTERNAL_EXPORT_FIELD_SUFFIXES)
    )


def redact_internal_fields(value: Any) -> Any:
    """Recursively drop internal-only secret/path fields from an export payload.

    The input is never mutated: a new structure of the same shape is returned
    with every :data:`INTERNAL_EXPORT_FIELDS` key (and any key carrying a
    secret/ciphertext suffix) removed at any nesting depth.
    """
    if isinstance(value, dict):
        return {
            key: redact_internal_fields(child)
            for key, child in value.items()
            if not _is_internal_export_field(key)
        }
    if isinstance(value, list):
        return [redact_internal_fields(child) for child in value]
    if isinstance(value, tuple):
        return tuple(redact_internal_fields(child) for child in value)
    return value


# Fields that carry a *third party's* personal data in DSAR rows. These are
# joined in by collectors to make the subject's own relationships readable
# (e.g. ``friend_username``), but the named person is not the data subject and
# has not consented to being disclosed in somebody else's export. GDPR
# Article 15(4) protects their rights, so the values are scrubbed unless an
# operator explicitly marks a field as legally required for disclosure.
THIRD_PARTY_PII_FIELDS: FrozenSet[str] = frozenset(
    {
        "friend_username",
        "sender_username",
        "recipient_username",
        "blocked_username",
        "reporter_username",
        "reported_username",
        "inviter_username",
        "target_username",
        "other_username",
        "friend_email",
        "sender_email",
        "recipient_email",
        "blocked_email",
        "reporter_email",
        "reported_email",
        "friend_display_name",
        "sender_display_name",
        "recipient_display_name",
        "other_display_name",
    }
)

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
        # Ownership columns used by collectors whose tables have no
        # ``user_id`` column. Without these, a collector that loses its
        # WHERE predicate returns foreign rows that carry NO recognised
        # subject field at all, so ``verify_export_scope`` treated them as
        # "no owner information" and let them straight into the export.
        "target_id",  # automod_exemptions (target_type='user')
        "installer_id",  # app_installations
        "uploaded_by",  # media_files
    }
)


class ExportScopeViolation(ValueError):
    """Raised when an export would contain another data subject's data."""


def _candidate_subject_ids(value: Any) -> Iterable[int]:
    """Yield integer ids from a field, tolerating string/float coded ids.

    Collected rows come from a database driver, so an ownership column may
    arrive as ``int``, as a digit string (some columns are TEXT), as a float
    (SQLite REAL affinity), or with surrounding whitespace. Treating any of
    those as "no owner" let a foreign row slip past :func:`verify_export_scope`
    simply because its id was encoded differently from the requesting user's.
    Normalise all of them here so the scope check cannot be bypassed by the
    storage type of an ownership column.
    """
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield value
    elif isinstance(value, float) and value.is_integer():
        yield int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            yield int(stripped)


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


def _iter_strings(value: Any) -> Iterator[str]:
    """Yield every string value in a (possibly nested) JSON-like structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_strings(child)


def _collect_pii_values(
    value: Any, fields: FrozenSet[str], legally_required: FrozenSet[str]
) -> Set[str]:
    """Find the concrete third-party values present in PII fields.

    These values (a friend's username, a reporter's email, ...) are also
    scrubbed from free-text fields so a message body that quotes or mentions
    the third party cannot smuggle their identity back into the export.
    """
    found: Set[str] = set()
    for mapping in _iter_mappings(value):
        for field in fields:
            if field in legally_required or field not in mapping:
                continue
            candidate = mapping[field]
            if isinstance(candidate, str) and len(candidate) >= 3:
                found.add(candidate)
    return found


def scrub_third_party_pii(
    value: Any,
    *,
    fields: FrozenSet[str] = THIRD_PARTY_PII_FIELDS,
    legally_required: Iterable[str] = (),
    redacted: str = REDACTED,
    scrub_inline: bool = True,
) -> Any:
    """Recursively remove third-party personal data from an export payload.

    Walks every mapping/list in the structure. Fields in ``fields`` have their
    value replaced with ``redacted``; additionally, any concrete value found
    in those fields is removed from free-text strings (longest first) so
    mentions such as ``"hey @friend_name"`` cannot leak the same identity.

    ``legally_required`` field names are preserved verbatim for deployments
    where disclosure is mandated (e.g. reporting a security incident). The
    caller supplies that set from configuration; the default is to redact
    everything.

    The input is never mutated: a new structure of the same shape is returned.
    """
    required = frozenset(legally_required)
    pii_values = _collect_pii_values(value, fields, required) if scrub_inline else set()
    # Longest first so an overlapping value cannot leave a longer name behind
    # after a shorter prefix has already been replaced.
    tokens: Tuple[str, ...] = tuple(sorted(pii_values, key=len, reverse=True))
    return _scrub(value, fields, required, redacted, tokens)


def _scrub(
    value: Any,
    fields: FrozenSet[str],
    legally_required: FrozenSet[str],
    redacted: str,
    tokens: Tuple[str, ...],
) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, child in value.items():
            if key in fields and key not in legally_required:
                result[key] = redacted
            else:
                result[key] = _scrub(child, fields, legally_required, redacted, tokens)
        return result
    if isinstance(value, list):
        return [
            _scrub(child, fields, legally_required, redacted, tokens) for child in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _scrub(child, fields, legally_required, redacted, tokens) for child in value
        )
    if isinstance(value, str):
        scrubbed = value
        for token in tokens:
            if token in scrubbed:
                scrubbed = scrubbed.replace(token, redacted)
        return scrubbed
    return value


class ExportQuotaExceeded(ValueError):
    """Raised when an export would exceed a configured resource bound."""


class ExportIncompleteError(ValueError):
    """Raised when collection failed partially and the export is incomplete."""


ENVELOPE_KEYS = frozenset(
    {"exported_at", "user_id", "export_version", "export_metadata"}
)


def _count_records(value: Any) -> int:
    """Count row-like records in one category payload."""
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        nested = sum(_count_records(child) for child in value.values())
        return nested if nested else 1
    return 0


def export_record_counts(data: Dict[str, Any]) -> Dict[str, int]:
    """Return {category: record_count} for every collected category."""
    return {
        category: _count_records(payload)
        for category, payload in data.items()
        if category not in ENVELOPE_KEYS
    }


def _export_serialized_size(data: Dict[str, Any]) -> int:
    """Upper bound of the bytes the generator will actually write.

    ``ExportFormatGenerator.generate_json`` does NOT write the compact JSON
    measured by a bare ``json.dumps(data)``. It wraps ``data`` in an envelope
    (request/user ids, generated_at, ...) and serialises with ``indent=2``.
    Pretty-printing routinely adds 1.5-2x overhead on nested collector rows,
    so measuring the compact form let an export blow well past
    ``max_export_bytes`` while the quota gate reported it as within bounds.

    Replicate the generator's envelope + indentation here (with worst-case
    snowflake-length numeric fields) so the byte quota is checked against the
    real artifact size. ZIP exports are bounded too: their compressed bytes
    are smaller than this uncompressed upper bound.
    """
    probe_envelope = {
        "export_type": "dsar_json",
        "export_version": "1.0",
        "request_id": 10**19 - 1,
        "user_id": 10**19 - 1,
        "generated_at": 10**10 - 1,
        "data": data,
    }
    return len(json.dumps(probe_envelope, indent=2, default=str).encode("utf-8"))


def enforce_export_quotas(
    data: Dict[str, Any],
    *,
    max_records_per_category: int,
    max_total_records: int,
    max_export_bytes: int,
) -> None:
    """Fail closed if ``data`` would produce an oversized export.

    Bounds every category independently, the overall record count, and the
    serialized payload size. A runaway collector query (a missing LIMIT, a
    self-referential join) can no longer exhaust memory or disk by generating
    an unbounded compliance artifact.

    Raises:
        ExportQuotaExceeded: describing the first bound that was breached.
    """
    counts = export_record_counts(data)
    for category, count in counts.items():
        if count > max_records_per_category:
            raise ExportQuotaExceeded(
                f"category {category!r} has {count} records, exceeding "
                f"max_records_per_category={max_records_per_category}"
            )

    total = sum(counts.values())
    if total > max_total_records:
        raise ExportQuotaExceeded(
            f"export has {total} records, exceeding "
            f"max_total_records={max_total_records}"
        )

    size = _export_serialized_size(data)
    if size > max_export_bytes:
        raise ExportQuotaExceeded(
            f"export payload is {size} bytes, exceeding "
            f"max_export_bytes={max_export_bytes}"
        )


def assert_export_complete(data: Dict[str, Any]) -> None:
    """Fail closed when any collector reported a partial failure.

    An incomplete compliance artifact is worse than no artifact: it tells the
    subject they received everything when categories are missing. Generation
    callers invoke this before writing any bytes so a partial collection
    leaves the request in ``failed`` instead of shipping a false-complete
    export.
    """
    metadata = data.get("export_metadata")
    if isinstance(metadata, dict) and metadata.get("complete") is False:
        warnings = metadata.get("warnings", [])
        raise ExportIncompleteError(
            "DSAR collection was incomplete; refusing to generate export: "
            + "; ".join(str(warning) for warning in warnings)
        )


def assert_export_ready(data: Dict[str, Any], quotas: Dict[str, int]) -> None:
    """All pre-generation gates in one call: complete + within quotas."""
    assert_export_complete(data)
    enforce_export_quotas(data, **quotas)


__all__ = [
    "REDACTED",
    "THIRD_PARTY_PII_FIELDS",
    "INTERNAL_EXPORT_FIELDS",
    "INTERNAL_EXPORT_FIELD_SUFFIXES",
    "redact_internal_fields",
    "ExportScopeViolation",
    "SUBJECT_ID_FIELDS",
    "verify_export_scope",
    "scrub_third_party_pii",
    "ExportQuotaExceeded",
    "ExportIncompleteError",
    "ENVELOPE_KEYS",
    "export_record_counts",
    "enforce_export_quotas",
    "assert_export_complete",
    "assert_export_ready",
]
