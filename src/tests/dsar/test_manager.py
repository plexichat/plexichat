"""DSARManager top-level flow: request, approve, deny, cancel, status."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def dsar_manager(db, auth_manager, pii_gen):
    from src.core.dsar import setup as dsar_setup
    from unittest.mock import patch
    from src.utils import encryption

    dsar_setup(db)

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="dsarmanuser",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        admin = auth_manager.register(
            username="dsaradmin",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return db, user, admin


class TestRequestExport:
    def test_request_export_creates_row(self, dsar_manager):
        db, user, _admin = dsar_manager
        from src.core.dsar import request_data_export

        result = request_data_export(user.id, format="json")
        assert result is not None
        assert result["user_id"] == user.id
        assert result["status"] == "pending"

    def test_get_user_requests(self, dsar_manager):
        db, user, _admin = dsar_manager
        from src.core.dsar import request_data_export, get_user_requests

        request_data_export(user.id)
        rows = get_user_requests(user.id)
        assert isinstance(rows, list)
        assert any(r["user_id"] == user.id for r in rows)

    def test_approve_request(self, dsar_manager):
        db, user, admin = dsar_manager
        from src.core.dsar import (
            request_data_export,
            approve_request,
        )

        req = request_data_export(user.id)
        approved = approve_request(req["id"], admin.id)
        assert approved["status"] == "approved"
        assert approved["admin_id"] == admin.id

    def test_cancel_request(self, dsar_manager):
        db, user, _admin = dsar_manager
        from src.core.dsar import request_data_export, cancel_request

        req = request_data_export(user.id)
        cancelled = cancel_request(req["id"], user.id)
        assert cancelled["status"] == "cancelled"

    def test_deny_request(self, dsar_manager):
        db, user, admin = dsar_manager
        from src.core.dsar import request_data_export, deny_request

        req = request_data_export(user.id)
        denied = deny_request(req["id"], admin.id, reason="test denial")
        assert denied["status"] == "denied"
        assert denied["denial_reason"] == "test denial"

    def test_get_request_status_authorization(self, dsar_manager):
        """Another user cannot see a request that isn't theirs."""
        db, user, _admin = dsar_manager
        from src.core.dsar import (
            request_data_export,
            get_request_status,
        )

        req = request_data_export(user.id)
        # Status for the OWNER is visible.
        assert get_request_status(req["id"], user.id) is not None
        # Status for SOMEONE ELSE should be None (auth boundary).
        assert get_request_status(req["id"], user.id + 9999) is None
