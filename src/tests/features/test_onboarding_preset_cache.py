"""The onboarding preset catalog is static and served from a cached response.

``GET /features/onboarding/presets`` used to rebuild the ``presets`` key list
on every request even though the catalog is immutable module data. Verify the
handler now returns the precomputed response object and that the wire shape is
unchanged.
"""

import uuid
from unittest.mock import patch

import pytest

from src.api.routes.features import onboarding
from src.utils import encryption

PRESETS_URL = "/api/v1/features/onboarding/presets"


def _register_and_login(auth_manager, label="preset_user"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token


@pytest.fixture
def auth_headers(auth_manager):
    _, token = _register_and_login(auth_manager)
    return {"Authorization": f"Bearer {token}"}


class TestOnboardingPresetCache:
    def test_endpoint_returns_unchanged_catalog_shape(self, test_client, auth_headers):
        resp = test_client.get(PRESETS_URL, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["presets"] == list(onboarding.ONBOARDING_PRESETS.keys())
        assert body["details"] == onboarding.ONBOARDING_PRESETS
        assert "gaming" in body["presets"]

    def test_handler_returns_cached_response_object(self):
        import asyncio

        result = asyncio.run(onboarding.list_onboarding_presets(current_user=object()))
        assert result is onboarding._ONBOARDING_PRESETS_RESPONSE

    def test_cached_response_matches_catalog(self):
        cached = onboarding._ONBOARDING_PRESETS_RESPONSE
        assert cached["presets"] == list(onboarding.ONBOARDING_PRESETS)
        assert cached["details"] is onboarding.ONBOARDING_PRESETS
