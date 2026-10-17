"""Licensing utils — proxy module re-export smoke tests."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestLicensingShim:
    def test_proxy_reexports_license_manager(self):
        from src.utils import licensing

        # The shim explicitly re-exports the singleton handle plus the
        # setup-state flags from common_utils.
        assert hasattr(licensing, "_license_manager")
        assert hasattr(licensing, "_setup_called")
        assert hasattr(licensing, "_free_tier_mode")
        assert hasattr(licensing, "_public_key_configured")

    def test_proxy_module_re_exports_from_common_utils(self):
        import src.utils.licensing as mod
        from src.utils.common_utils.utils import licensing as common_licensing

        # Every public name the source module advertises in __all__ must
        # be visible through the shim so static analysis sees the same API.
        assert common_licensing.__all__, "licensing must declare __all__"
        for name in common_licensing.__all__:
            assert hasattr(mod, name), f"shim is missing {name}"
