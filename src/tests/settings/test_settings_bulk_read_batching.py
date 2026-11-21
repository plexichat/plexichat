"""Query-budget regressions for the settings read path.

Reading several known keys for one user used to loop over ``get()``, issuing
one ``WHERE user_id = ? AND key = ?`` query per key (the read-side N+1).
:meth:`SettingsManager.get_many` now resolves them in bounded, user-scoped
``key IN (...)`` chunks. These tests pin:

* **result parity** with the replaced per-key loop (including ``None`` for
  missing/unreadable keys and server-managed keys);
* **query-count reduction** (N reads -> ceil(N / 500));
* **bounds** (never more than 500 keys bound in a single ``IN`` clause);
* **no cross-user leak** (the batched lookup always carries the ``user_id``
  predicate, so another user's value is invisible — fail closed).
"""

import pytest

from src.core.settings.manager import SettingsManager


class _CountingDB:
    """Delegate to the real DB while recording every read statement."""

    def __init__(self, inner):
        self._inner = inner
        self.reads = []  # (kind, sql, params)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def fetch_one(self, sql, params=()):
        self.reads.append(("fetch_one", sql, tuple(params)))
        return self._inner.fetch_one(sql, params)

    def fetch_all(self, sql, params=()):
        self.reads.append(("fetch_all", sql, tuple(params)))
        return self._inner.fetch_all(sql, params)


def _batched_reads(counted):
    return [r for r in counted.reads if "key IN (" in r[1]]


def _seed(db, manager, uid, key, value):
    now = manager._get_timestamp()
    db.execute(
        "INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (manager._generate_id(), uid, key, value, now, now),
    )


def test_many_read_result_parity_with_per_key_loop(db, two_users):
    """get_many returns exactly what a loop of get() calls returned."""
    manager = SettingsManager(db)
    alice, bob = two_users[0].id, two_users[1].id
    for uid, key, value in (
        (alice, "alpha", "a1"),
        (alice, "beta", "a2"),
        (bob, "alpha", "b1"),
    ):
        _seed(db, manager, uid, key, value)
    # A server-managed key stays readable through the batched path too.
    manager.set_server_setting(alice, "_focused_channel", "{}")

    keys = ["alpha", "beta", "gamma", "_focused_channel"]
    for uid in (alice, bob):
        batched = manager.get_many(uid, keys)
        # The replaced implementation: one query per key, same predicate.
        naive = {key: manager.get(uid, key) for key in keys}
        assert batched == naive

    assert manager.get_many(alice, keys) == {
        "alpha": "a1",
        "beta": "a2",
        "gamma": None,
        "_focused_channel": "{}",
    }
    assert manager.get_many(bob, keys)["alpha"] == "b1"


def test_many_read_query_count_drops_from_n_to_chunks(db, test_user):
    """Measure the reduction: N per-key reads -> ceil(N / 500) batched reads."""
    uid = test_user.id
    manager = SettingsManager(db)
    keys = [f"k{i}" for i in range(1200)]
    for key in keys:
        _seed(db, manager, uid, key, "v")

    counted = _CountingDB(db)
    count_manager = SettingsManager(counted)

    for key in keys:
        count_manager.get(uid, key)
    naive_reads = len(counted.reads)
    assert naive_reads == 1200  # the old N+1: one query per key

    counted.reads.clear()
    result = count_manager.get_many(uid, keys)
    batched_reads = len(counted.reads)

    assert len(result) == 1200
    assert set(count_manager.get_all(uid)) == set(keys)
    # ceil(1200 / 500) == 3, compared to 1200 before.
    assert batched_reads == 3
    assert batched_reads < naive_reads
    print(
        f"\nquery reduction: before={naive_reads} per-key reads, "
        f"after={batched_reads} batched reads "
        f"({naive_reads / batched_reads:.1f}x fewer)"
    )


def test_many_read_small_batch_is_single_query(db, test_user):
    counted = _CountingDB(db)
    manager = SettingsManager(counted)
    uid = test_user.id
    for i in range(40):
        _seed(db, manager, uid, f"k{i}", f"v{i}")

    counted.reads.clear()
    result = manager.get_many(uid, [f"k{i}" for i in range(40)])

    assert len(result) == 40
    assert len(counted.reads) == 1
    assert len(_batched_reads(counted)) == 1
    assert all(kind == "fetch_all" for kind, _, _ in counted.reads)
    # No per-key ``... WHERE user_id = ? AND key = ?`` probe survives.
    assert not any(
        kind == "fetch_one" and "key = ?" in sql for kind, sql, _ in counted.reads
    )


def test_many_read_binds_at_most_500_keys(db, test_user):
    uid = test_user.id
    keys = [f"k{i}" for i in range(1200)]
    counted = _CountingDB(db)
    count_manager = SettingsManager(counted)

    count_manager.get_many(uid, keys)

    batched = _batched_reads(counted)
    assert len(batched) == 3
    for _, sql, params in batched:
        # First bind is the requesting user; the IN list itself is <= 500.
        assert "user_id = ?" in sql
        assert params[0] == uid
        assert 1 <= len(params) - 1 <= 500
        assert sql.count("?") == len(params)


def test_many_read_never_crosses_user_boundary(db, two_users):
    manager = SettingsManager(db)
    alice, bob = two_users[0].id, two_users[1].id
    _seed(db, manager, alice, "shared", "alice-secret")

    counted = _CountingDB(db)
    bob_manager = SettingsManager(counted)
    result = bob_manager.get_many(bob, ["shared"])

    assert result == {"shared": None}
    assert manager.get(alice, "shared") == "alice-secret"

    batched = _batched_reads(counted)
    assert len(batched) == 1
    assert "user_id = ?" in batched[0][1]
    assert batched[0][2][0] == bob


def test_many_read_skips_unreadable_legacy_keys(db, test_user):
    manager = SettingsManager(db)
    uid = test_user.id
    _seed(db, manager, uid, "good_key", "v1")
    _seed(db, manager, uid, "junk:key with spaces", "stale")

    result = manager.get_many(uid, ["good_key", "junk:key with spaces"])

    assert result == {"good_key": "v1", "junk:key with spaces": None}
    # Parity with the per-key getter, which also rejects the legacy row.
    assert result["junk:key with spaces"] == manager.get(uid, "junk:key with spaces")


def test_many_read_empty_keys_issues_no_query(db, test_user):
    counted = _CountingDB(db)
    manager = SettingsManager(counted)

    assert manager.get_many(test_user.id, []) == {}
    assert counted.reads == []


@pytest.mark.unit
def test_bulk_key_chunk_size_stays_500():
    from src.core.settings.manager import _BULK_KEY_CHUNK_SIZE

    assert _BULK_KEY_CHUNK_SIZE == 500
