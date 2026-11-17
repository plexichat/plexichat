"""Regression tests for residual DSAR hardening gaps.

Covers three fail-open paths that let a non-compliant export through:

* scope verification ignored ownership columns used by real collectors,
  and silently skipped ids encoded as floats or padded strings;
* the byte quota measured compact JSON while the generator writes a
  pretty-printed, enveloped artifact that is substantially larger;
* the per-record ``integrity_hash`` was written by export/deletion events
  but never actually verified, so metadata tampering went undetected.
"""

from __future__ import annotations

import hashlib
import json

import pytest

ALICE = 1001
BOB = 2002


class TestScopeOwnershipColumns:
    @pytest.mark.parametrize("field", ["installer_id", "uploaded_by", "target_id"])
    def test_foreign_row_on_unlisted_ownership_column_is_rejected(self, field):
        from src.core.dsar.privacy import ExportScopeViolation, verify_export_scope

        # These columns are the ONLY ownership pointer in their source table
        # (app_installations, media_files, automod_exemptions). Before the fix
        # the row had no recognised subject field, so the guard skipped it.
        data = {
            "user_id": ALICE,
            "apps": {"installations": [{"id": 1, field: BOB, "content": "bob's"}]},
        }
        with pytest.raises(ExportScopeViolation):
            verify_export_scope(data, ALICE)

    @pytest.mark.parametrize("foreign_id", [2002.0, " 2002 ", "2002"])
    def test_foreign_id_with_alternate_encoding_is_rejected(self, foreign_id):
        from src.core.dsar.privacy import ExportScopeViolation, verify_export_scope

        data = {"user_id": ALICE, "sessions": {"s": [{"user_id": foreign_id}]}}
        with pytest.raises(ExportScopeViolation):
            verify_export_scope(data, ALICE)

    def test_subject_still_accepted_alongside_foreign_pointer(self):
        from src.core.dsar.privacy import verify_export_scope

        data = {
            "user_id": ALICE,
            "apps": {"installations": [{"id": 1, "installer_id": ALICE, "app": BOB}]},
        }
        verify_export_scope(data, ALICE)


class TestQuotaMeasuresRealArtifact:
    def test_pretty_envelope_overhead_is_counted(self):
        from src.core.dsar.privacy import (
            ExportQuotaExceeded,
            enforce_export_quotas,
        )

        data = {
            "messages": {
                "messages": [
                    {"id": i, "content": "x" * 40, "author_id": ALICE}
                    for i in range(25)
                ]
            }
        }
        compact = len(json.dumps(data, default=str).encode("utf-8"))
        pretty = len(
            json.dumps(
                {
                    "export_type": "dsar_json",
                    "export_version": "1.0",
                    "request_id": 10**19 - 1,
                    "user_id": 10**19 - 1,
                    "generated_at": 10**10 - 1,
                    "data": data,
                },
                indent=2,
                default=str,
            ).encode("utf-8")
        )
        assert pretty > compact
        # A limit between the two sizes used to be (incorrectly) accepted.
        limit = (compact + pretty) // 2
        with pytest.raises(ExportQuotaExceeded):
            enforce_export_quotas(
                data,
                max_records_per_category=1000,
                max_total_records=1000,
                max_export_bytes=limit,
            )

    def test_small_export_still_allowed(self):
        from src.core.dsar.privacy import enforce_export_quotas

        enforce_export_quotas(
            {"identity": {"id": ALICE}},
            max_records_per_category=100,
            max_total_records=200,
            max_export_bytes=100_000,
        )


class TestAuditIntegrityHashVerified:
    def test_tampered_metadata_with_recomputed_chain_is_rejected(self, tmp_path):
        from src.core.dsar.audit_log import DSARLog

        path = tmp_path / "audit.jsonl"
        log = DSARLog(file_path=str(path))
        log.log_export_event(7, 123, "a" * 64, 42, "local", "dsar/x.json")
        assert log.verify_chain() == (True, 1, None)

        entry = json.loads(path.read_text().splitlines()[0])
        # Attacker flips the recorded artifact checksum but keeps the chain
        # checksum valid by recomputing the unkeyed SHA-256 themselves.
        entry["metadata"]["artifact_sha256"] = "b" * 64
        entry.pop("checksum")
        content = json.dumps(entry, sort_keys=True)
        entry["checksum"] = hashlib.sha256(
            ("0" * 64 + content).encode("utf-8")
        ).hexdigest()
        path.write_text(json.dumps(entry) + "\n")

        valid, _count, error = log.verify_chain()
        assert valid is False
        assert "integrity_hash" in error

    def test_valid_integrity_event_still_verifies(self, tmp_path):
        from src.core.dsar.audit_log import DSARLog

        path = tmp_path / "audit.jsonl"
        log = DSARLog(file_path=str(path))
        log.log_deletion_event(7, 123, "local", "dsar/x.json", True)
        assert log.verify_chain() == (True, 1, None)
