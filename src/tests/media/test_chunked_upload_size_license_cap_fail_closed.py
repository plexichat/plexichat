"""Chunked-upload license size cap must fail closed.

``ChunkedUploadManager._validate_total_size`` applies the licensed
``max_file_size_mb`` ceiling on top of the configured per-type limit, matching
the direct-upload path in ``_ValidationMixin._validate_file_size``. Previously
an uninitialised license manager or a missing licensing module raised inside
that gate and was swallowed by ``except (RuntimeError, ImportError): pass``,
which silently dropped the license ceiling and admitted up to the (looser)
configured size through ``POST /media/upload/session``. Free tier never
evaluates a license limit, so the only caller that can hit the failure path is
a licensed instance whose cap is expected.

These tests prove the gate denies a chunked session when the license cannot be
evaluated, and that the licensed/free-tier verdicts are unchanged.
"""

import sys
from unittest.mock import patch

import pytest

import utils.licensing as licensing
from src.core.media import chunked as chunked_mod
from src.core.media.chunked import ChunkedUploadManager

pytestmark = [pytest.mark.unit, pytest.mark.security]

_MB = 1024 * 1024

# ``_validate_total_size`` reads the global media config (not instance state),
# so pin it to a deliberately loose per-type limit. The license cap is stricter
# and is what must be honoured.
_MEDIA_CONFIG = {"size_limits": {"image": 100 * _MB}}


def _validate(content_type="image/png", total_size=_MB):
    """Call the size gate with a pinned (loose) configured limit."""
    with patch.object(chunked_mod.config, "get", return_value=_MEDIA_CONFIG):
        mgr = object.__new__(ChunkedUploadManager)
        return mgr._validate_total_size(content_type, total_size)


def _license_unavailable(monkeypatch):
    """Make ``from utils.licensing import ...`` raise RuntimeError mid-gate."""

    def _boom(*args, **kwargs):
        raise RuntimeError("licensing not configured")

    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", _boom)


def test_chunked_session_denied_when_license_check_raises(monkeypatch):
    """A licensed instance must not admit a chunked session when its cap
    cannot be verified (regression: the error was swallowed and the file was
    accepted up to the loose 100 MiB configured limit)."""
    _license_unavailable(monkeypatch)

    assert _validate(total_size=_MB) is False  # 1 MiB is well under 100 MiB


def test_chunked_session_denied_when_licensing_module_missing():
    """A missing licensing module must fail closed, not drop the cap."""
    with patch.dict(sys.modules, {"utils.licensing": None}):
        assert _validate(total_size=_MB) is False


def test_finite_license_cap_is_still_enforced(monkeypatch):
    """A healthy licensed gate keeps enforcing the license's own cap."""
    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", lambda *a, **k: 1)

    assert _validate(total_size=2 * _MB) is False  # over the 1 MiB license cap
    assert _validate(total_size=_MB // 2) is True  # under it


def test_free_tier_keeps_configured_limit(monkeypatch):
    """Free tier never evaluates a license cap; configured limits still apply."""
    monkeypatch.setattr(licensing, "is_free_tier", lambda: True)

    assert _validate(total_size=2 * _MB) is True  # within the configured 100 MiB
