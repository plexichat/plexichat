"""Perf + security regression guards for the storage/media/artifacts reads.

Covers the five hot endpoints that used to fan out into extra database reads or
return unbounded data:

* ``GET /storage/quota`` and ``/storage/usage`` share one aggregate query.
* ``GET /storage/files`` keeps its path-traversal and pagination bounds.
* ``GET /media/upload/sessions`` returns a bounded page.
* ``GET /artifacts/{id}/ops`` authorizes from a focused scope read and keeps
  its IDOR / pagination guards.

The tests pin the *cold* query fan-out with a counting DB adapter and check that
the optimized code returns exactly the same data as the original multi-query
logic. Authorization and validation stay on the real code path.
"""

import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.core.auth.models import AccountType, TokenInfo
from src.core.security.hardening import HardeningViolation

pytestmark = [pytest.mark.unit]


def _user(user_id: int = 42) -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


class _CountingDB:
    """Delegate to the real DB while counting reads and capturing SQL."""

    def __init__(self, db):
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "reads", 0)
        object.__setattr__(self, "sql", [])
        object.__setattr__(self, "params", [])

    def __getattr__(self, name):
        attr = getattr(self.db, name)

        def wrapped(*args, **kwargs):
            if name in ("fetch_all", "fetch_one"):
                object.__setattr__(self, "reads", self.reads + 1)
                object.__setattr__(self, "sql", self.sql + [args[0] if args else ""])
                params = kwargs.get("params", args[1] if len(args) > 1 else ())
                object.__setattr__(self, "params", self.params + [params])
            return attr(*args, **kwargs)

        return wrapped


def _insert_media(db, now):
    db.execute("DELETE FROM media_files")
    for i in range(20):
        db.execute(
            "INSERT INTO media_files (uploaded_by, filename, original_filename, "
            "content_type, media_type, storage_backend, size, storage_path, "
            "uploaded_at, deleted) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                42 if i % 2 == 0 else 99,
                f"f{i}.bin",
                f"f{i}.bin",
                "application/octet-stream",
                "file",
                "local",
                1024,
                f"uploads/42/f{i}.bin",
                now,
                1 if i == 19 else 0,
            ),
        )


# --- quota / usage ---------------------------------------------------------


def test_quota_status_single_query_and_identical_output(db, monkeypatch):
    from src.core.storage_quota.manager import StorageQuotaManager

    now = int(time.time() * 1000)
    _insert_media(db, now)

    # One live and one expired reservation; also an admin override.
    mgr0 = StorageQuotaManager(db, config_override={"default_quota_mb": 4})
    mgr0._ensure_schema()
    db.execute(
        "INSERT INTO storage_quota_reservations "
        "(token, user_id, bytes, created_at, expires_at) VALUES (?,?,?,?,?)",
        ("live", 42, 2048, now, now + 10**9),
    )
    db.execute(
        "INSERT INTO storage_quota_reservations "
        "(token, user_id, bytes, created_at, expires_at) VALUES (?,?,?,?,?)",
        ("expired", 42, 99999, 0, 1),
    )

    def _make(db_obj):
        m = StorageQuotaManager(db_obj, config_override={"default_quota_mb": 4})
        m._user_tier = lambda _uid: "standard"  # pyright: ignore[reportAttributeAccessIssue]
        m._tier_limit_bytes = lambda _tier: None  # pyright: ignore[reportAttributeAccessIssue]
        return m

    # Reference: the original four independent reads, assembled by hand.
    ref = _make(db)
    resolved = ref.resolve_limit(42)
    expected = {
        "used_bytes": ref.usage_bytes(42),
        "pending_bytes": ref.pending_bytes(42),
        "limit_bytes": resolved["limit_bytes"],
        "remaining_bytes": None,
        "percent_used": 0.0,
        "unlimited": bool(resolved["unlimited"]),
        "file_count": ref.file_count(42),
        "source": resolved["source"],
        "tier": resolved["tier"],
        "tier_limit_bytes": resolved["tier_limit_bytes"],
        "default_limit_bytes": resolved["default_limit_bytes"],
    }
    used = expected["used_bytes"] + expected["pending_bytes"]
    limit = expected["limit_bytes"]
    expected["remaining_bytes"] = max(0, int(limit) - used)
    expected["percent_used"] = min(
        100.0, max(0.0, round((used * 100.0 / limit), 2))
    )

    counting = _CountingDB(db)
    optimized = _make(counting)
    counting.reads = 0
    got = optimized.get_status(42)

    assert counting.reads == 1, counting.sql
    assert got == expected

    # Override path (including unlimited and zero-byte caps) is parsed from
    # the single aggregate query exactly as before.
    ref.set_override(42, limit_bytes=0)
    zero = _make(_CountingDB(db)).get_status(42)
    assert zero["source"] == "override"
    assert zero["limit_bytes"] == 0
    assert zero["remaining_bytes"] == 0
    assert zero["percent_used"] == 100.0
    ref.set_override(42, unlimited=True)
    unlimited = _make(_CountingDB(db)).get_status(42)
    assert unlimited["unlimited"] is True
    assert unlimited["limit_bytes"] is None
    assert unlimited["remaining_bytes"] is None


@pytest.mark.asyncio
async def test_storage_usage_alias_matches_quota(db, monkeypatch):
    from src.api.routes import storage as route

    monkeypatch.setattr(api, "_db", db)
    monkeypatch.setattr(api, "get_db", lambda: db)
    import src.core.storage_quota.manager as sq

    sq._manager_cache.clear()
    quota = await route.get_my_storage_quota(current_user=_user())
    usage = await route.get_my_storage_usage(current_user=_user())
    assert quota == usage


# --- storage files ---------------------------------------------------------


def test_storage_files_bounds_and_path_traversal(db):
    from src.core.storage_quota.manager import StorageQuotaManager

    now = int(time.time() * 1000)
    _insert_media(db, now)
    mgr = StorageQuotaManager(db, config_override={"default_quota_mb": 4})

    page = mgr.list_user_files(42, limit=5, offset=2)
    assert len(page["files"]) <= 5
    assert page["limit"] == 5 and page["offset"] == 2
    # Ids are stringified; only the caller's live rows are returned.
    assert all(isinstance(f["id"], str) for f in page["files"])
    assert all("uploads/42/" in f["storage_path"] for f in page["files"])

    for bad in ("../etc/passwd", "/abs/path", "a/../../b", "x\x00y"):
        with pytest.raises(HardeningViolation):
            mgr.list_user_files(42, path_prefix=bad)

    # LIKE wildcards are not rejected but escaped, so they cannot widen the
    # filter beyond the caller's own path prefix.
    counting = _CountingDB(db)
    StorageQuotaManager(
        counting, config_override={"default_quota_mb": 4}
    ).list_user_files(42, path_prefix="100%_x")
    escaped_param = " ".join(str(p) for p in counting.params[-1])
    assert "\\%" in escaped_param and "\\_" in escaped_param

    with pytest.raises(HardeningViolation):
        mgr.list_user_files(42, limit=10**9)
    with pytest.raises(HardeningViolation):
        mgr.list_user_files(42, offset=-1)


@pytest.mark.asyncio
async def test_storage_files_handler_rejects_traversal(db, monkeypatch):
    from src.api.routes import storage as route

    monkeypatch.setattr(api, "_db", db)
    monkeypatch.setattr(api, "get_db", lambda: db)
    import src.core.storage_quota.manager as sq

    sq._manager_cache.clear()
    with pytest.raises(HTTPException) as exc:
        await route.list_my_storage_files(
            limit=50, offset=0, path="../outside", current_user=_user()
        )
    assert exc.value.status_code == 400


# --- upload sessions -------------------------------------------------------


def test_upload_sessions_are_bounded_and_shape_stable(db, monkeypatch):
    import src.core.media as media_mod

    media_mod.setup(db)
    from src.core.media.chunked import MAX_USER_SESSIONS

    now = int(time.time() * 1000)
    db.execute("DELETE FROM media_upload_sessions")
    for i in range(MAX_USER_SESSIONS + 25):
        db.execute(
            "INSERT INTO media_upload_sessions (id, user_id, filename, content_type, "
            "total_size, chunk_size, total_chunks, uploaded_chunks, uploaded_bytes, "
            "status, created_at, updated_at, expires_at, temp_path, checksum) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"s-{i:04d}-{'a' * 20}",
                42,
                f"f{i}.bin",
                "application/octet-stream",
                10240,
                1024,
                10,
                3,
                3072,
                "in_progress",
                now + i,
                now + i,
                now + 600000,
                f"/tmp/chunk-{i}",
                None,
            ),
        )

    from src.core.media.chunked import ChunkedUploadManager

    # A fresh manager over a counting adapter proves the LIMIT reaches the
    # database (no unbounded scan) without disturbing the shared singleton.
    counting = _CountingDB(db)
    counted_mgr = ChunkedUploadManager(counting)
    sessions = counted_mgr.get_user_sessions(42)
    assert len(sessions) == MAX_USER_SESSIONS
    assert "LIMIT" in counting.sql[-1].upper()

    mgr = media_mod._get_chunked_manager()
    assert len(mgr.get_user_sessions(42, limit=5)) == 5
    with pytest.raises(HardeningViolation):
        mgr.get_user_sessions(42, limit=MAX_USER_SESSIONS + 1)


@pytest.mark.asyncio
async def test_upload_sessions_handler_bounded(db, monkeypatch):
    import src.core.media as media_mod

    media_mod.setup(db)
    from src.api.routes import media as route

    now = int(time.time() * 1000)
    db.execute("DELETE FROM media_upload_sessions")
    for i in range(30):
        db.execute(
            "INSERT INTO media_upload_sessions (id, user_id, filename, content_type, "
            "total_size, chunk_size, total_chunks, uploaded_chunks, uploaded_bytes, "
            "status, created_at, updated_at, expires_at, temp_path, checksum) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"h-{i:04d}-{'b' * 20}",
                42,
                f"h{i}.bin",
                "image/png",
                2048,
                512,
                4,
                2,
                1024,
                "in_progress",
                now + i,
                now + i,
                now + 600000,
                None,
                None,
            ),
        )

    resp = await route.get_user_upload_sessions(user=_user(), limit=7)
    assert len(resp.sessions) == 7
    first = resp.sessions[0]
    assert first.progress_percent == 50
    assert {"session_id", "filename", "status", "expires_at"} <= set(
        first.model_dump().keys()
    )
    with pytest.raises(HTTPException):
        await route.get_user_upload_sessions(user=_user(), limit=10**6)


# --- artifact ops ----------------------------------------------------------


def test_artifact_ops_focused_read_and_idor(db, monkeypatch):
    import src.api.routes.artifacts_helpers as helpers
    from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState
    from src.core.artifacts.manager import ArtifactManager
    from src.core.artifacts.models import ArtifactType

    def _available(feature, _cfg=None):
        return CapabilityInfo(feature, CapabilityState.AVAILABLE, "enabled")

    monkeypatch.setattr(helpers, "get_capability", _available)

    mgr = ArtifactManager(db, {})
    art = mgr.create(
        conversation_id=None,
        artifact_type=ArtifactType.FILE,
        author_id=42,
        title="probe",
        payload={"blob": "x" * 50_000},
        emit_event=False,
    )
    for i in range(15):
        mgr.append_op(art.id, "edit", 42, {"i": i})

    # The focused scope read must not select the payload column.
    counting = _CountingDB(db)
    monkeypatch.setattr(api, "get_db", lambda: counting)
    scope = helpers._get_artifact_scope(art.id)
    assert scope is not None
    assert scope.author_id == 42 and scope.artifact_type == ArtifactType.FILE
    assert "payload" not in counting.sql[-1].lower()
    assert helpers._get_artifact_scope(10**9 + 7) is None

    # A different, unauthorized user cannot read the ops (no membership, no
    # authorship, no server scope): the user-scope artifact stays private.
    from src.api.routes.artifacts.routes import exports as route

    monkeypatch.setattr(helpers, "_get_manager", lambda: ArtifactManager(db, {}))
    with patch.object(api, "get_servers", return_value=None), patch.object(
        api, "get_messaging", return_value=None
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                route._list_artifact_ops_impl(
                    str(art.id), after_seq=0, limit=500, current_user=_user(777)
                )
            )
    assert exc.value.status_code == 403

    # Repository-level bounds: negative after_seq is clamped and an oversized
    # limit is capped, so a crafted request can never dump the whole log.
    from src.core.artifacts.repository import list_artifact_ops

    clamped = list_artifact_ops(db, art.id, after_seq=-5, limit=10**9)
    assert len(clamped) == 15
    assert all(op["seq"] > 0 for op in clamped)
