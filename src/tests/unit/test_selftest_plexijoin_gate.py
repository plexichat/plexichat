"""The self-test must only treat the *licence* denial as expected.

``RouteDiscovery.expected_plexijoin_denial`` decides whether a non-2xx on a
PlexiJoin-gated route is the licence gate refusing (expected on a free-tier
install) or a real failure. If it accepted any 403 on a gated path, a genuine
authorization regression -- an admin losing ``admin.plexijoin.manage``, which
the routes check *before* the licence -- would be masked as an expected
denial. These tests pin the body check that prevents that, using the real
licensing module (no mocks).
"""

import pytest

import src.utils.common_utils.utils.licensing as licensing
from src.core.selftest.services.discovery import RouteDiscovery


@pytest.fixture
def licensing_state():
    """Run against the real module, restoring its globals afterwards.

    ``licensing.setup()`` mutates process-wide state; saving and restoring the
    named globals in the ``finally`` keeps the suite order-independent.
    """
    saved = (
        licensing._license_manager,
        licensing._free_tier_mode,
        licensing._setup_called,
    )
    try:
        yield licensing
    finally:
        (
            licensing._license_manager,
            licensing._free_tier_mode,
            licensing._setup_called,
        ) = saved


GATED = "/api/v1/admin/plexijoin/connections"
LICENSE_BODY = '{"detail":{"error":{"message":"PlexiJoin feature is not licensed"}}}'
AUTHZ_BODY = '{"detail":"Access denied"}'


@pytest.mark.unit
def test_is_plexijoin_gated_matches_only_gated_routes():
    discovery = RouteDiscovery(None)
    assert discovery.is_plexijoin_gated(GATED)
    assert discovery.is_plexijoin_gated("/api/v1/federation/artifact-op")
    assert not discovery.is_plexijoin_gated("/api/v1/admin/license/status")
    assert not discovery.is_plexijoin_gated("/api/v1/channels/1/messages")


@pytest.mark.unit
def test_unlicensed_licence_denial_is_expected(licensing_state):
    """A 403 whose body names the licence gate is an expected denial."""
    licensing.setup()
    licensing._free_tier_mode = True
    discovery = RouteDiscovery(None)
    assert discovery.expected_plexijoin_denial(GATED, 403, LICENSE_BODY) is True


@pytest.mark.unit
def test_unlicensed_authorization_403_is_not_masked(licensing_state):
    """The gap being closed: an authorization 403 must NOT be expected."""
    licensing.setup()
    licensing._free_tier_mode = True
    discovery = RouteDiscovery(None)
    assert discovery.expected_plexijoin_denial(GATED, 403, AUTHZ_BODY) is False


@pytest.mark.unit
def test_success_and_non_gated_are_not_denials(licensing_state):
    licensing.setup()
    licensing._free_tier_mode = True
    discovery = RouteDiscovery(None)
    # A 2xx is never a denial.
    assert discovery.expected_plexijoin_denial(GATED, 200, LICENSE_BODY) is False
    # A licence body on a NON-gated path is not a PlexiJoin denial.
    assert (
        discovery.expected_plexijoin_denial(
            "/api/v1/channels/1/messages", 403, LICENSE_BODY
        )
        is False
    )


@pytest.mark.unit
def test_licensed_install_expects_no_denial(licensing_state):
    """With the feature licensed, the expected-status set is empty."""
    licensing.setup()
    assert licensing.apply_test_license()["success"] is True
    discovery = RouteDiscovery(None)
    assert discovery.expected_plexijoin_denial(GATED, 403, LICENSE_BODY) is False
