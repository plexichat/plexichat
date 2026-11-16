"""Boundary tests for TLS certificate path confinement (commit 6)."""

import os
from pathlib import Path

import pytest

from src.core.tls.path_validation import (
    TLSPathError,
    validate_cert_path,
    validate_certificate_pair,
)


@pytest.fixture
def roots(tmp_path):
    base = tmp_path / "certs"
    base.mkdir()
    return str(base)


def _make(base, name, parent=None):
    target = Path(parent or base) / name
    target.write_text("x")
    return str(target)


class TestValidPath:
    def test_accepts_confined_file(self, roots):
        p = _make(roots, "server.crt")
        assert validate_cert_path(p, allowed_roots=[roots]) == os.path.realpath(p)

    def test_accepts_custom_suffix(self, roots):
        p = _make(roots, "server.foo")
        assert validate_cert_path(p, allowed_roots=[roots], expected_suffixes=[".foo"])


class TestConfinement:
    def test_rejects_dotdot_escape(self, roots, tmp_path):
        outside = _make(str(tmp_path), "outside.crt")
        rel = os.path.join(roots, "..", os.path.basename(outside))
        with pytest.raises(TLSPathError):
            validate_cert_path(rel, allowed_roots=[roots])

    def test_rejects_sibling_directory(self, roots, tmp_path):
        sibling = tmp_path / (os.path.basename(roots) + "-evil")
        sibling.mkdir()
        p = _make(str(sibling), "server.crt")
        with pytest.raises(TLSPathError):
            validate_cert_path(p, allowed_roots=[roots])

    def test_rejects_symlink_escape(self, roots, tmp_path):
        outside = _make(str(tmp_path), "outside.crt")
        link = os.path.join(roots, "link.crt")
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("symlinks unavailable")
        with pytest.raises(TLSPathError):
            validate_cert_path(link, allowed_roots=[roots])


class TestTokens:
    @pytest.mark.parametrize(
        "bad", ["a\x00b.crt", "a\rb.crt", "a\nb.crt", "a\x7fb.crt"]
    )
    def test_forbidden_tokens(self, roots, bad):
        with pytest.raises(TLSPathError):
            validate_cert_path(os.path.join(roots, bad), allowed_roots=[roots])

    def test_empty_and_non_string(self, roots):
        for bad in ("", "   ", None, 123):
            with pytest.raises(TLSPathError):
                validate_cert_path(bad, allowed_roots=[roots])

    def test_bad_suffix(self, roots):
        p = _make(roots, "server.exe")
        with pytest.raises(TLSPathError):
            validate_cert_path(p, allowed_roots=[roots])

    def test_too_long(self, roots):
        with pytest.raises(TLSPathError):
            validate_cert_path("a" * 5000, allowed_roots=[roots])


class TestPair:
    def test_requires_distinct_files(self, roots):
        p = _make(roots, "same.crt")
        with pytest.raises(TLSPathError):
            validate_certificate_pair(p, p, allowed_roots=[roots])

    def test_valid_pair(self, roots):
        cert = _make(roots, "server.crt")
        key = _make(roots, "server.key")
        assert validate_certificate_pair(cert, key, allowed_roots=[roots]) == (
            os.path.realpath(cert),
            os.path.realpath(key),
        )
