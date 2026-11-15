"""Invariants for the defense-in-depth helpers in ``core.security.hardening``.

Each helper exists because a call site interpolated an untrusted value into
something with a syntax of its own: an HTTP header, a ``PRAGMA``/``ALTER``
statement, a filesystem path, or an integer literal. These tests pin the
answers so a future caller cannot loosen them by accident.
"""

import os

import pytest

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    content_disposition,
    is_identifier,
    reject_control_characters,
    require_identifier,
    safe_join,
    sanitize_header_filename,
)


class TestHeaderFilename:
    def test_control_characters_cannot_split_the_header(self):
        assert sanitize_header_filename("a\r\nX-Injected: 1.txt") == "aX-Injected_1.txt"

    def test_quotes_and_separators_are_collapsed(self):
        assert sanitize_header_filename('he"llo wörld.txt') == "he_llo_w_rld.txt"

    def test_path_components_are_dropped(self):
        assert sanitize_header_filename("../../etc/passwd") == "passwd"
        assert sanitize_header_filename("C:\\windows\\evil.exe") == "evil.exe"

    def test_empty_and_dot_names_fall_back(self):
        assert sanitize_header_filename("") == "attachment"
        assert sanitize_header_filename("..") == "attachment"
        assert sanitize_header_filename(None) == "attachment"

    def test_length_is_bounded(self):
        assert len(sanitize_header_filename("a" * 500)) == 120

    def test_disposition_has_no_bare_quotes(self):
        value = content_disposition('a"; filename="b')
        assert value.startswith('attachment; filename="')
        assert value.count('"') == 2


class TestContentDisposition:
    def test_inline_and_attachment(self):
        assert content_disposition("x.txt") == 'attachment; filename="x.txt"'
        assert content_disposition("x.txt", inline=True) == 'inline; filename="x.txt"'


class TestIdentifiers:
    @pytest.mark.parametrize("name", ["users", "msg_messages", "_tmp", "a1"])
    def test_valid(self, name):
        assert is_identifier(name)
        assert require_identifier(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "1users",
            "users; DROP TABLE x",
            "users--",
            "a b",
            "a.b",
            None,
            7,
            "a" * 70,
        ],
    )
    def test_invalid(self, name):
        assert not is_identifier(name)
        with pytest.raises(HardeningViolation):
            require_identifier(name)


class TestSafeJoin:
    def test_inside_base(self, tmp_path):
        assert safe_join(str(tmp_path), "a", "b.txt") == os.path.join(
            str(tmp_path), "a", "b.txt"
        )

    @pytest.mark.parametrize("path", ["../escape", "a/../../escape", "/etc/passwd"])
    def test_escapes_are_refused(self, tmp_path, path):
        with pytest.raises(HardeningViolation):
            safe_join(str(tmp_path), path)

    def test_symlink_escape_is_refused(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        base = tmp_path / "base"
        base.mkdir()
        (base / "link").symlink_to(outside, target_is_directory=True)
        with pytest.raises(HardeningViolation):
            safe_join(str(base), "link", "file.txt")


class TestBoundedInt:
    def test_accepts_in_range(self):
        assert bounded_int("42", "x", minimum=0, maximum=100) == 42

    def test_default_when_missing(self):
        assert bounded_int(None, "x", minimum=0, maximum=10, default=7) == 7

    @pytest.mark.parametrize("value", ["abc", "1; DROP TABLE x", []])
    def test_rejects_non_integers(self, value):
        with pytest.raises(HardeningViolation):
            bounded_int(value, "x", minimum=0, maximum=10)

    @pytest.mark.parametrize("value", [-1, 11, 2**70])
    def test_rejects_out_of_range(self, value):
        with pytest.raises(HardeningViolation):
            bounded_int(value, "x", minimum=0, maximum=10)


class TestControlCharacters:
    def test_plain_value_passes(self):
        assert reject_control_characters("abc123", "checksum") == "abc123"

    @pytest.mark.parametrize("value", ["a\r\nb", "a\x00b", "a\x7fb"])
    def test_rejects_control_characters(self, value):
        with pytest.raises(HardeningViolation):
            reject_control_characters(value, "checksum")


def test_hardened_call_sites_use_the_helpers():
    """The primitives only help if the risky call sites actually call them."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    expectations = {
        "src/api/media_serving.py": "content_disposition",
        "src/api/routes/users/dsar.py": "content_disposition",
        "src/api/routes/admin/telemetry.py": "content_disposition",
        "src/core/migrations/cloner.py": "require_identifier",
        "src/core/messaging/schema.py": "require_identifier",
        "src/core/push/manager/base.py": "require_identifier",
        "src/core/database/engines/sqlite.py": "bounded_int",
        "src/core/suite/sum/_shared.py": "safe_join",
    }
    for relative, marker in expectations.items():
        source = (root / relative).read_text(encoding="utf-8")
        assert marker in source, f"{relative} stopped using {marker}"
