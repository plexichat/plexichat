"""Boundary tests for audit field bounds and secret redaction (commit 9)."""

from src.core.security.audit import (
    REDACTED,
    bound_field,
    redact_text,
    safe_log_line,
    sanitise_audit_entry,
)


class TestRedactText:
    def test_bearer(self):
        assert "abc123tokenxyz" not in redact_text(
            "Authorization: Bearer abc123tokenxyz"
        )

    def test_key_value(self):
        out = redact_text("password=hunter2")
        assert "hunter2" not in out
        assert REDACTED in out

    def test_clean_text_unchanged(self):
        assert redact_text("user logged in") == "user logged in"


class TestBoundField:
    def test_control_characters_stripped(self):
        assert "\n" not in bound_field("a\r\nb")
        assert "\x00" not in bound_field("a\x00b")

    def test_length(self):
        out = bound_field("a" * 2000, max_length=100)
        assert "[truncated]" in out

    def test_redacts_in_value(self):
        assert "secretXYZ" not in bound_field("token=secretXYZ")


class TestAuditEntry:
    def test_secret_keys_redacted(self):
        entry = sanitise_audit_entry(
            {"user": "alice", "password": "hunter2", "access_token": "abc"}
        )
        assert entry["user"] == "alice"
        assert entry["password"] == REDACTED
        assert entry["access_token"] == REDACTED

    def test_control_in_key(self):
        entry = sanitise_audit_entry({"a\nb": "v"})
        assert "a\nb" not in entry
        assert "ab" in entry

    def test_field_count_bounded(self):
        data = {f"f{i}": str(i) for i in range(100)}
        entry = sanitise_audit_entry(data, max_fields=5)
        assert len(entry) <= 6  # 5 fields + truncation marker
        assert "_truncated_fields" in entry

    def test_values_bounded(self):
        entry = sanitise_audit_entry({"note": "a" * 5000}, max_value_length=100)
        assert len(entry["note"]) < 200

    def test_deterministic_keys(self):
        a = sanitise_audit_entry({"b": "2", "a": "1"})
        assert list(a) == ["a", "b"]


class TestSafeLogLine:
    def test_single_line(self):
        line = safe_log_line("a\nb", "c\rd")
        assert "\n" not in line and "\r" not in line

    def test_joins(self):
        assert safe_log_line("GET", "/path", 200) == "GET /path 200"
