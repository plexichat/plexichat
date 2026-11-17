"""Query-count and config-resolution budgets for the DSAR export paths.

Latency ceilings are noisy; query counts and config resolutions are not. These
tests pin the shape of the three hot paths behind:

* ``POST /users/@me/data-export`` (password check + request creation),
* ``POST /admin/data-export/{id}/generate`` (manual generation),
* the shared generation core used by the background harvester.

Generation used to re-read and re-merge the whole DSAR config tree several
times per export (once for the quota gate, once again in the collector for the
privacy scrubber, plus a raw lookup for retention). The caller now resolves the
config once and threads the snapshot through; these guards keep it that way
without weakening the quota/privacy gates.
"""

from __future__ import annotations

import uuid

import pytest

import src.api as api

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def gen_env(db, auth_manager, pii_gen, tmp_path, monkeypatch):
    """A DSAR setup with one subject and one approved export request."""
    from src.core.dsar import request_data_export, setup as dsar_setup
    from src.core.dsar.audit_log import DSARLog
    from src.core.dsar.manager import DSARManager
    from unittest.mock import patch
    from src.utils import encryption

    dsar_setup(db)
    monkeypatch.setattr(
        DSARManager,
        "_get_audit_log",
        lambda self: DSARLog(file_path=str(tmp_path / "budget-audit.jsonl")),
    )
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        subject = auth_manager.register(
            username=f"budget_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    req = request_data_export(subject.id, format="json")
    db.execute(
        "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
    )
    return db, subject, req["id"]


def _clear(db):
    from src.core.database.cache.base import mem_cache_clear_pattern

    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


# ---------------------------------------------------------------------------
# generation config resolution
# ---------------------------------------------------------------------------


def test_generate_manual_resolves_dsar_config_once(gen_env, monkeypatch):
    """The whole generation path resolves the DSAR config tree exactly once.

    ``get_dsar_config()`` is the only caller of ``config.get("authentication")``;
    storage-backend and audit-log lookups use different keys. Counting that one
    key therefore counts config-tree resolutions precisely.
    """
    import utils.config as config

    db, _subject, req_id = gen_env
    base_get = config.get
    seen = {"authentication": 0}

    def counting_get(key, default=None):
        if key == "authentication":
            seen["authentication"] += 1
        return base_get(key, default)

    monkeypatch.setattr(config, "get", counting_get)

    from src.core.dsar.manager import DSARManager

    result = DSARManager(db).generate_manual(req_id, admin_id=1)

    assert result is not None
    assert seen["authentication"] == 1, (
        "generate_manual must resolve the DSAR config once and reuse the "
        f"snapshot; got {seen['authentication']} resolutions"
    )


def test_generation_still_enforces_quota_with_reused_snapshot(
    gen_env, monkeypatch
):
    """Threading the config snapshot must not bypass the quota gate."""
    from src.core.dsar.privacy import ExportQuotaExceeded
    import src.core.dsar.manager as manager_module

    db, _subject, req_id = gen_env
    monkeypatch.setattr(
        manager_module,
        "export_quotas",
        lambda *a, **k: {
            "max_records_per_category": 1,
            "max_total_records": 1,
            "max_export_bytes": 1,
        },
    )

    with pytest.raises(ExportQuotaExceeded):
        manager_module.DSARManager(db).generate_manual(req_id, admin_id=1)

    row = dict(db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (req_id,)))
    assert row["status"] == "failed"
    assert not row["storage_path"]


# ---------------------------------------------------------------------------
# request creation query count
# ---------------------------------------------------------------------------


def test_request_export_query_budget(gen_env):
    """Creating an export request is a constant, small number of queries."""
    db, subject, _req_id = gen_env
    from src.core.dsar.manager import DSARManager

    _clear(db)
    result = DSARManager(db).request_export(subject.id, format="json")
    count = db.get_request_metrics()["query_count"]
    assert result is not None
    assert count <= 4, f"request_export issued {count} queries, budget is 4"


# ---------------------------------------------------------------------------
# password verification query count (POST /users/@me/data-export)
# ---------------------------------------------------------------------------


class _OneRowDB:
    """Database stub that records every statement it is asked to run."""

    def __init__(self, row):
        self.row = row
        self.queries = []

    def fetch_one(self, query, params=()):
        self.queries.append((query, params))
        return self.row


def test_password_verify_uses_a_single_query(monkeypatch):
    """Presence check and password hash load share one auth_users lookup."""
    from src.api.routes.users.dsar import DataExportMixin

    db = _OneRowDB({"password_hash": "hash-for-test"})
    monkeypatch.setattr(api, "get_auth", lambda: object())
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(
        "src.utils.encryption.verify_password", lambda pw, hashed: True
    )

    assert DataExportMixin()._verify_password(42, "pw") is True
    assert len(db.queries) == 1, (
        "password verification must issue one auth_users query, "
        f"got {len(db.queries)}"
    )
    assert "password_hash" in db.queries[0][0]
    assert db.queries[0][1] == (42,)


# ---------------------------------------------------------------------------
# privacy scrubber honours the supplied snapshot
# ---------------------------------------------------------------------------


def test_collector_reuses_config_snapshot_for_privacy(db, auth_manager, pii_gen):
    """Passing a resolved config snapshot keeps third-party redaction opt-ins."""
    import time

    from src.core.dsar.collector import DataCollector
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        alice = auth_manager.register(
            username=f"snap_a_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        bob = auth_manager.register(
            username=f"snap_b_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    db.execute(
        "INSERT INTO rel_friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
        (alice.id, bob.id, int(time.time())),
    )

    config_snapshot = {"privacy": {"legally_required_pii_fields": ["friend_username"]}}
    export = DataCollector(db, config=config_snapshot).collect_all(alice.id)
    assert export["relationships"]["friends"][0]["friend_username"] == bob.username


# ---------------------------------------------------------------------------
# batched expiry / deletion queries
# ---------------------------------------------------------------------------


def test_expired_cleanup_query_count_is_batched(db, monkeypatch):
    """Expiring N exports is a constant number of queries, not N+1."""
    import time

    from src.core.dsar import setup as dsar_setup
    from src.core.dsar.harvester import DSARHarvester
    from src.core.dsar.manager import DSARManager
    from src.utils.encryption import generate_snowflake_id

    dsar_setup(db)
    # Isolate the DB shape of the expiry path from storage/audit side effects.
    monkeypatch.setattr(DSARManager, "_scrub_export_files", lambda self, r: True)
    monkeypatch.setattr(DSARHarvester, "_sweep_orphaned_files", lambda self: None)

    harvester = DSARHarvester(db)
    now = int(time.time())

    def seed(n):
        ids = []
        for _ in range(n):
            rid = generate_snowflake_id()
            db.execute(
                "INSERT INTO dsar_requests "
                "(id, user_id, status, requested_at, format, expires_at) "
                "VALUES (?, ?, 'ready', ?, 'json', ?)",
                (rid, 1, now, now - 10),
            )
            ids.append(rid)
        return ids

    small_ids = seed(2)
    _clear(db)
    harvester._cleanup_expired()
    small_count = db.get_request_metrics()["query_count"]

    large_ids = seed(20)
    _clear(db)
    harvester._cleanup_expired()
    large_count = db.get_request_metrics()["query_count"]

    assert small_count <= 3, f"small expiry batch issued {small_count} queries"
    assert large_count <= small_count, (
        "expiry query count must not grow with the number of rows: "
        f"2 rows -> {small_count}, 20 rows -> {large_count}"
    )
    expired_rows = db.fetch_all(
        "SELECT id FROM dsar_requests WHERE status = 'expired' AND id IN "
        f"({','.join('?' for _ in small_ids + large_ids)})",
        tuple(small_ids + large_ids),
    )
    assert len(expired_rows) == len(small_ids) + len(large_ids)
