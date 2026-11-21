"""Upload size license gate must fail closed.

``_ValidationMixin._validate_file_size`` applies the licensed
``max_file_size_mb`` ceiling on top of the configured per-type limit. Previously
an uninitialised license manager or a missing licensing module raised inside
that gate and was swallowed by ``except (RuntimeError, ImportError): pass``,
which silently dropped the license ceiling and admitted up to the (looser)
configured size. Free tier never evaluates a license limit, so the only caller
that can hit the failure path is a licensed instance whose cap is expected.

These tests prove the gate denies uploads when the license cannot be evaluated,
and that the licensed/free-tier verdicts are unchanged.
"""

import sys
from unittest.mock import patch

import pytest

import utils.licensing as licensing
from src.core.media._validation import _ValidationMixin
from src.core.media.exceptions import FileSizeError
from src.core.media.models import MediaType

pytestmark = [pytest.mark.unit, pytest.mark.security]

_MB = 1024 * 1024


class _Host(_ValidationMixin):
    def __init__(self, config):
        self._config = config


def _license_unavailable(monkeypatch):
    """Make ``from utils.licensing import ...`` raise RuntimeError mid-gate."""

    def _boom(*args, **kwargs):
        raise RuntimeError("licensing not configured")

    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", _boom)


def test_upload_denied_when_license_check_raises(monkeypatch):
    host = _Host({"size_limits": {"image": 100 * _MB}})
    _license_unavailable(monkeypatch)

    with pytest.raises(FileSizeError):
        host._validate_file_size(_MB, MediaType.IMAGE)


def test_upload_denied_when_licensing_module_missing():
    host = _Host({"size_limits": {"image": 100 * _MB}})
    # A None entry in sys.modules makes the import in the gate raise ImportError.
    with patch.dict(sys.modules, {"utils.licensing": None}):
        with pytest.raises(FileSizeError):
            host._validate_file_size(_MB, MediaType.IMAGE)


def test_finite_license_cap_is_still_enforced(monkeypatch):
    """A healthy, licensed gate keeps enforcing the license's own cap."""
    host = _Host({"size_limits": {"image": 100 * _MB}})
    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", lambda *a, **k: 1)

    with pytest.raises(FileSizeError) as exc:
        host._validate_file_size(2 * _MB, MediaType.IMAGE)

    assert exc.value.max_size == 1 * _MB


def test_free_tier_keeps_configured_limit(monkeypatch):
    """Free tier never evaluates a license cap; configured limits still apply."""
    host = _Host({"size_limits": {"image": 2 * _MB}})
    monkeypatch.setattr(licensing, "is_free_tier", lambda: True)

    host._validate_file_size(_MB, MediaType.IMAGE)  # within the configured cap

    with pytest.raises(FileSizeError):
        host._validate_file_size(3 * _MB, MediaType.IMAGE)
