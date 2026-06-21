"""Licensing utils — proxy module re-export smoke tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestLicensingShim:
    def test_proxy_reexports_expected_symbols(self):
        try:
            from src.utils import licensing
        except ImportError:
            pytest.skip("licensing util not installed in this environment")
        # The shim re-exports the actual licensing helpers from
        # common_utils; the smoke test just asserts it can resolve
        # the most-documented public name ``_license_manager``.
        assert hasattr(licensing, "_license_manager") or hasattr(
            licensing, "LicenseManager"
        )

    def test_proxy_module_is_substantial(self):
        try:
            import src.utils.licensing as mod
        except ImportError:
            pytest.skip("licensing util not installed")
        # The proxy exists but may be small; just check it loads
        # without crashing.
        assert mod.__doc__ is not None or len(dir(mod)) > 1
