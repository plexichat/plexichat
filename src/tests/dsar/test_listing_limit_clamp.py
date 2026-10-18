"""Regression tests: DSAR listing bounds must be clamped from below.

SQLite treats ``LIMIT -1`` as *unlimited*, so before the fix
``GET /api/v1/admin/data-export?limit=-1`` — and the core
``get_admin_requests`` it delegates to — returned every DSAR row (the full
export history for every user) instead of an empty page. A negative
``offset`` was forwarded to SQL verbatim as well.

The harvester had the mirror-image problem: its configured ``batch_size``
reached SQL unchanged, so a non-positive value exported every approved
request in one cycle (``LIMIT -1``) and ``LIMIT 0`` stalled the worker
forever. Both paths now clamp to a safe range.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def dsar_listing(db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from src.utils import encryption

    dsar_setup(db)
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="dsarlistuser",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, user


def _seed_requests(user_id, count=3):
    from src.core.dsar import request_data_export

    for _ in range(count):
        request_data_export(user_id)


class TestAdminListingClamp:
    def test_negative_limit_returns_empty(self, dsar_listing):
        _db, user = dsar_listing
        from src.core.dsar import get_admin_requests

        _seed_requests(user.id)
        # LIMIT -1 must never dump the whole table.
        assert get_admin_requests(limit=-1) == []

    def test_zero_limit_returns_empty(self, dsar_listing):
        _db, user = dsar_listing
        from src.core.dsar import get_admin_requests

        _seed_requests(user.id)
        assert get_admin_requests(limit=0) == []

    def test_default_limit_returns_rows(self, dsar_listing):
        _db, user = dsar_listing
        from src.core.dsar import get_admin_requests

        _seed_requests(user.id)
        assert len(get_admin_requests()) == 3

    def test_negative_offset_behaves_like_zero(self, dsar_listing):
        _db, user = dsar_listing
        from src.core.dsar import get_admin_requests

        _seed_requests(user.id)
        # A negative offset is clamped to 0, so the first page is returned
        # rather than being forwarded to SQL verbatim.
        assert len(get_admin_requests(offset=-5)) == len(get_admin_requests(offset=0))

    def test_limit_above_cap_still_returns_page(self, dsar_listing):
        _db, user = dsar_listing
        from src.core.dsar import get_admin_requests
        from src.core.dsar.manager import MAX_ADMIN_PAGE_SIZE

        _seed_requests(user.id)
        rows = get_admin_requests(limit=MAX_ADMIN_PAGE_SIZE * 10)
        assert len(rows) == 3


class TestHarvesterBatchSizeClamp:
    def test_zero_batch_size_still_processes_a_request(self, dsar_listing):
        db, user = dsar_listing
        from src.core.dsar import request_data_export
        from src.core.dsar.harvester import DSARHarvester

        req = request_data_export(user.id)
        harvester = DSARHarvester(db)
        harvester._harvester_config["batch_size"] = 0
        harvester._harvester_config["require_admin_review"] = False
        harvester.harvest()
        row = db.fetch_one(
            "SELECT status FROM dsar_requests WHERE id = ?", (req["id"],)
        )
        # LIMIT 0 would leave the request untouched forever.
        assert row["status"] != "pending"

    def test_negative_batch_size_clamped_to_one(self, dsar_listing):
        db, user = dsar_listing
        from src.core.dsar.harvester import DSARHarvester

        _seed_requests(user.id)
        harvester = DSARHarvester(db)
        harvester._harvester_config["batch_size"] = -1
        harvester._harvester_config["require_admin_review"] = False

        processed = []

        def _record(request):
            processed.append(request["id"])

        harvester._process_request = _record
        harvester.harvest()
        # LIMIT -1 would have processed all three.
        assert len(processed) == 1
