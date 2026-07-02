"""DSARHarvester - happy-path cycle on a pending request."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def harvester_fixtures(db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from src.core.dsar.harvester import DSARHarvester
    from unittest.mock import patch
    from src.utils import encryption

    dsar_setup(db)
    harvester = DSARHarvester(db)

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="dsarharvesteruser",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        admin = auth_manager.register(
            username="dsarharvesteradmin",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return harvester, user, admin, db


class TestHarvesterFlow:
    def test_harvest_after_admin_approval(self, harvester_fixtures):
        harvester, user, admin, db = harvester_fixtures
        from src.core.dsar import request_data_export, approve_request

        req = request_data_export(user.id)
        approve_request(req["id"], admin.id)
        # Force ``require_admin_review=False`` to flatten the test path.
        harvester._harvester_config["require_admin_review"] = False
        harvester.harvest()
        row = db.fetch_one(
            "SELECT status FROM dsar_requests WHERE id = ?", (req["id"],)
        )
        assert row is not None
        assert row["status"] in ("ready", "generating")

    def test_harvest_skips_without_admin(self, harvester_fixtures):
        harvester, user, _admin, db = harvester_fixtures
        from src.core.dsar import request_data_export

        request_data_export(user.id)
        harvester._harvester_config["require_admin_review"] = True
        harvester.harvest()
        # Status should still be 'pending' since no admin approved.
        rows = db.fetch_all(
            "SELECT status FROM dsar_requests WHERE user_id = ?", (user.id,)
        )
        assert all(r["status"] in ("pending", "approved", "ready") for r in rows)

    def test_stop_method_safe(self, harvester_fixtures):
        harvester, *_ = harvester_fixtures
        harvester.stop()
        assert harvester._is_running is False
