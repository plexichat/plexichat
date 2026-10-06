"""Admin PlexiJoin + federation inbound id serialization.

The connection and inbound-request ``id`` values are ``INTEGER PRIMARY KEY``
rowids (``src/core/plexijoin/schema.py``) and stay numeric. The
``created_by``/``reviewed_by`` columns hold 64-bit admin snowflakes, which are
served as decimal strings because a JS ``Number`` cannot hold them exactly.
The federation ``/join-request`` reply carries the new row's rowid, so it is
numeric too. These tests pin both halves of that contract over HTTP-shaped
calls to the real handlers.
"""

import pytest

import src.api.routes.admin.plexijoin as admin_plexijoin
import src.api.routes.federation as federation_module


class _FakeClient:
    host = "10.0.0.1"


class _FakeRequest:
    def __init__(self, body=None):
        self._body = body or {}
        self.client = _FakeClient()
        self.headers = {}

    async def json(self):
        return self._body


class _FakeManager:
    def __init__(self, connection=None, requests=None, request_row=None):
        self._connection = connection
        self._requests = requests or {
            "requests": [],
            "total": 0,
            "page": 1,
            "per_page": 50,
            "pages": 0,
        }
        self._request_row = request_row

    def list_connections(self, **kwargs):
        return {
            "connections": [self._connection],
            "total": 1,
            "page": 1,
            "per_page": 50,
            "pages": 1,
        }

    def get_connection(self, connection_id):
        return self._connection

    def create_connection(self, **kwargs):
        return self._connection

    def list_requests(self, **kwargs):
        return self._requests

    def approve_request(self, **kwargs):
        return self._request_row

    def deny_request(self, **kwargs):
        return self._request_row


# INTEGER PRIMARY KEY rowid (stays numeric).
ROWID = 7
# 2**53-ish admin snowflake (must be a decimal string).
BIG_ADMIN = 987654321098765432


def _patch_admin(monkeypatch, manager):
    monkeypatch.setattr(admin_plexijoin, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(admin_plexijoin, "get_admin_from_token", lambda request: 7)
    monkeypatch.setattr(
        admin_plexijoin, "require_admin_permission", lambda request, perm: 7
    )
    monkeypatch.setattr(
        admin_plexijoin, "_get_plexijoin_manager", lambda request: manager
    )


def _connection_row():
    return {
        "id": ROWID,
        "remote_instance_id": "remote:example",
        "remote_url": "https://remote.example",
        "status": "pending",
        "connected_at": None,
        "messages_in": 0,
        "messages_out": 0,
        "last_activity": None,
        "note": None,
        "created_at": 123,
        "created_by": BIG_ADMIN,
    }


def _request_row():
    return {
        "id": ROWID,
        "remote_instance_id": "remote:example",
        "remote_url": "https://remote.example",
        "requested_by": "someone",
        "note": None,
        "status": "approved",
        "requested_at": 123,
        "reviewed_at": 456,
        "reviewed_by": 777,
    }


@pytest.mark.asyncio
async def test_admin_list_connections_keeps_rowid_numeric(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(connection=_connection_row()))
    result = await admin_plexijoin.list_connections(_FakeRequest())
    conn = result["connections"][0]
    assert conn["id"] == ROWID
    assert conn["created_by"] == str(BIG_ADMIN)


@pytest.mark.asyncio
async def test_admin_get_connection_keeps_rowid_numeric(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(connection=_connection_row()))
    result = await admin_plexijoin.get_connection(_FakeRequest(), connection_id=ROWID)
    assert result["id"] == ROWID
    assert result["created_by"] == str(BIG_ADMIN)


@pytest.mark.asyncio
async def test_admin_create_connection_keeps_rowid_numeric(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(connection=_connection_row()))
    request = _FakeRequest(
        {
            "remote_instance_id": "remote:example",
            "remote_url": "https://remote.example",
            "shared_key": "sekret",
        }
    )
    result = await admin_plexijoin.create_connection(request)
    assert result["connection_id"] == ROWID


@pytest.mark.asyncio
async def test_admin_list_requests_keeps_rowid_numeric(monkeypatch):
    row = _request_row()
    pending = dict(row, status="pending", reviewed_by=None, reviewed_at=None)
    manager = _FakeManager(
        requests={
            "requests": [pending],
            "total": 1,
            "page": 1,
            "per_page": 50,
            "pages": 1,
        }
    )
    _patch_admin(monkeypatch, manager)
    result = await admin_plexijoin.list_requests(_FakeRequest())
    req = result["requests"][0]
    assert req["id"] == ROWID
    assert req["reviewed_by"] is None


@pytest.mark.asyncio
async def test_admin_approve_deny_keep_rowid_numeric(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(request_row=_request_row()))
    for handler in (admin_plexijoin.approve_request, admin_plexijoin.deny_request):
        result = await handler(_FakeRequest(), request_id=ROWID)
        req = result["request"]
        assert req["id"] == ROWID
        assert req["reviewed_by"] == "777"


def test_serialize_helpers_keep_rowids_numeric():
    conn = admin_plexijoin._serialize_connection(_connection_row())
    assert conn["id"] == ROWID
    assert conn["created_by"] == str(BIG_ADMIN)
    req = admin_plexijoin._serialize_inbound_request(_request_row())
    assert req["id"] == ROWID
    assert req["reviewed_by"] == "777"


@pytest.mark.asyncio
async def test_federation_join_request_inbound_keeps_rowid_numeric(monkeypatch):
    manager = _FakeManager()

    async def _fake_body(request):
        return b'{"instance_id": "remote:example"}'

    class _InboundRequest:
        headers = {
            "x-plexijoin-signature": "sig",
            "x-plexijoin-timestamp": "1",
            "x-plexijoin-nonce": "n",
        }

        async def body(self):
            return await _fake_body(self)

    submitted = {}

    def _submit(remote_instance_id, remote_url, requested_by=None, note=None):
        submitted.update({"remote_instance_id": remote_instance_id})
        return dict(_request_row(), status="pending")

    manager.submit_inbound_request = _submit  # type: ignore[method-assign]
    monkeypatch.setattr(federation_module, "_get_plexijoin_manager", lambda: manager)
    monkeypatch.setattr(
        federation_module, "_authenticate", lambda *args, **kwargs: {"id": 1}
    )
    result = await federation_module.join_request_inbound(_InboundRequest())
    assert result["ok"] is True
    assert result["request_id"] == ROWID
    assert submitted["remote_instance_id"] == "remote:example"
