"""Security invariants: DSAR exports contain no third-party personal data.

Pure, data-driven checks against the recursive scrubber and the scope guard
that every DSAR export passes before bytes are written. No database and no
clock: each case is a static JSON-like payload.

* Every declared third-party PII field is replaced with the redaction marker,
  at any nesting depth, and concrete third-party values are removed from
  free-text mentions as well.
* The subject's own identity fields are preserved.
* A collector that drops its ``WHERE user_id = ?`` predicate is caught: an
  envelope stamped for somebody else, or a row owned solely by a different
  subject, fails closed.
"""

from __future__ import annotations

import json

import pytest

from src.core.dsar.privacy import (
    REDACTED,
    SUBJECT_ID_FIELDS,
    THIRD_PARTY_PII_FIELDS,
    ExportScopeViolation,
    scrub_third_party_pii,
    verify_export_scope,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

_SENTINELS = {
    field: f"THIRDPARTY_{index}_{field}"
    for index, field in enumerate(sorted(THIRD_PARTY_PII_FIELDS))
}
_SUBJECT = 100


def _payload_with(field: str) -> dict:
    return {
        "user_id": _SUBJECT,
        "nested": {"rows": [{"user_id": _SUBJECT, field: _SENTINELS[field]}]},
    }


def _serialized(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class TestEveryDeclaredPiiFieldIsRedacted:
    @pytest.mark.parametrize("field", sorted(THIRD_PARTY_PII_FIELDS))
    def test_field_is_redacted_at_any_depth(self, field):
        scrubbed = scrub_third_party_pii(_payload_with(field))
        assert scrubbed["nested"]["rows"][0][field] == REDACTED
        assert _SENTINELS[field] not in _serialized(scrubbed)

    def test_all_fields_and_inline_mentions_in_one_payload(self):
        rows = [{**{f: _SENTINELS[f] for f in THIRD_PARTY_PII_FIELDS}}]
        mention = "hi @" + _SENTINELS["friend_username"] + " cc " + _SENTINELS["friend_email"]
        payload = {
            "user_id": _SUBJECT,
            "identity": {"id": _SUBJECT, "username": "subject_user", "email": "subject@example.test"},
            "relationships": {"friends": rows},
            "messages": {"messages": [{"author_id": _SUBJECT, "content": mention}]},
        }
        before = _serialized(payload)
        scrubbed = scrub_third_party_pii(payload)
        # Input is never mutated.
        assert _serialized(payload) == before

        for field in THIRD_PARTY_PII_FIELDS:
            assert scrubbed["relationships"]["friends"][0][field] == REDACTED
        for field, sentinel in _SENTINELS.items():
            assert sentinel not in _serialized(scrubbed), field
        # The subject's own identity is still readable.
        assert scrubbed["identity"]["username"] == "subject_user"
        assert scrubbed["identity"]["email"] == "subject@example.test"

    def test_longest_mention_is_removed_first(self):
        payload = {
            "user_id": _SUBJECT,
            "friend_username": "Alex",
            "friend_display_name": "Alexander",
            "chat": [{"content": "ping Alexander and Alex"}],
        }
        scrubbed = scrub_third_party_pii(payload)
        content = scrubbed["chat"][0]["content"]
        assert "Alexander" not in content
        assert "Alex" not in content
        assert "[redacted] and [redacted]" in content

    @pytest.mark.parametrize("field", ["friend_username", "friend_email", "sender_username"])
    def test_legally_required_is_opt_in_and_narrow(self, field):
        payload = _payload_with(field)
        kept = scrub_third_party_pii(payload, legally_required=[field])
        assert kept["nested"]["rows"][0][field] == _SENTINELS[field]
        default = scrub_third_party_pii(payload)
        assert default["nested"]["rows"][0][field] == REDACTED

    def test_disabling_inline_scrub_still_redacts_fields(self):
        payload = _payload_with("friend_username")
        scrubbed = scrub_third_party_pii(payload, scrub_inline=False)
        assert scrubbed["nested"]["rows"][0]["friend_username"] == REDACTED
        assert _SENTINELS["friend_username"] not in _serialized(scrubbed)


class TestExportScopeFailsClosed:
    @pytest.mark.parametrize(
        "data",
        [
            {"user_id": 999, "identity": {"id": 999}},
            {"user_id": _SUBJECT, "identity": {"id": 999}},
            {"user_id": _SUBJECT, "messages": [{"user_id": 999, "content": "x"}]},
            {"user_id": _SUBJECT, "messages": [{"author_id": "999", "content": "x"}]},
            {"user_id": _SUBJECT, "nested": {"rows": [{"recipient_id": 999}]}},
            {
                "user_id": _SUBJECT,
                "nested": {"rows": [{"author_id": 999, "user_id": 999}]},
            },
        ],
    )
    def test_foreign_owned_export_rejected(self, data):
        with pytest.raises(ExportScopeViolation):
            verify_export_scope(data, _SUBJECT)

    @pytest.mark.parametrize(
        "data",
        [
            {"user_id": _SUBJECT},
            {"user_id": _SUBJECT, "identity": {"id": _SUBJECT, "username": "subject"}},
            {"user_id": _SUBJECT, "messages": [{"user_id": _SUBJECT, "content": "x"}]},
            {"user_id": _SUBJECT, "messages": [{"author_id": str(_SUBJECT)}]},
            # A pointer to another user is not ownership of a foreign row.
            {"user_id": _SUBJECT, "friends": [{"user_id": _SUBJECT, "friend_id": 999}]},
        ],
    )
    def test_in_scope_export_accepted(self, data):
        verify_export_scope(data, _SUBJECT)

    @pytest.mark.parametrize("field", sorted(SUBJECT_ID_FIELDS))
    def test_every_subject_id_field_is_checked(self, field):
        verify_export_scope(
            {"user_id": _SUBJECT, "rows": [{field: _SUBJECT}]}, _SUBJECT
        )
        with pytest.raises(ExportScopeViolation):
            verify_export_scope({"user_id": _SUBJECT, "rows": [{field: 999}]}, _SUBJECT)
