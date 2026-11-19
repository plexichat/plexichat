"""Query-budget regressions for ``SettingsManager.set_many``.

The bulk write used to issue one ``_exists`` probe plus one ``fetch_one`` per
key (2N + 1 reads). It now resolves existing rows in bounded user-scoped
``IN (...)`` chunks. These tests pin:

* **result parity** with the replaced per-key lookup (including updates that
  must preserve ``id``/``created_at``);
* **query-count reduction** (O(1) for a normal batch, O(ceil(N/500)) for a
  huge one);
* **bounds** (never more than 500 keys bound in a single ``IN`` clause);
* **no cross-user leak** (the batched lookup is always scoped by ``user_id``,
  so another user's key is treated as absent and a fresh row is inserted).
"""

import pytest

from src.core.settings.manager import SettingsConfig, SettingsManager
from src.core.settings.models import UserSetting


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


def _huge_config(n):
    return SettingsConfig(
        max_settings_per_user=n,
        max_bulk_entries=n,
        max_bulk_payload_bytes=10_000_000,
    )


def test_bulk_lookup_result_parity_with_replaced_loop(db, two_users):
    """The batched lookup returns exactly what the old per-key loop did."""
    manager = SettingsManager(db)
    alice, bob = two_users[0].id, two_users[1].id
    now = manager._get_timestamp()

    # Alice owns two keys; Bob owns one of the same names with other values.
    for uid, key, value in (
        (alice, "alpha", "a1"),
        (alice, "beta", "a2"),
        (bob, "alpha", "b1"),
    ):
        db.execute(
            "INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (manager._generate_id(), uid, key, value, now, now),
        )

    keys = ["alpha", "beta", "gamma"]
    for uid in (alice, bob):
        batched = {
            k: (row["id"], row["created_at"])
            for k, row in manager._get_existing_bulk(uid, keys).items()
        }
        # The replaced implementation: one fetch per key, same predicate.
        naive = {}
        for key in keys:
            row = db.fetch_one(
                "SELECT id, created_at FROM user_settings WHERE user_id = ? AND key = ?",
                (uid, key),
            )
            if row:
                naive[key] = (row["id"], row["created_at"])
        assert batched == naive

    assert set(manager._get_existing_bulk(alice, keys)) == {"alpha", "beta"}
    assert set(manager._get_existing_bulk(bob, keys)) == {"alpha"}


def test_set_many_read_count_is_constant_not_per_key(db, test_user):
    counted = _CountingDB(db)
    manager = SettingsManager(counted)
    uid = test_user.id
    items = {f"k{i}": f"v{i}" for i in range(40)}

    results = manager.set_many(uid, items)

    assert set(results) == set(items)
    # One COUNT for the limit check + one batched IN lookup. No _exists or
    # per-key fetch_one may survive in the write loop.
    assert len(counted.reads) == 2
    assert len(_batched_reads(counted)) == 1
    assert all(kind == "fetch_all" for kind, _, _ in _batched_reads(counted))
    # The only fetch_one left is the aggregate limit check, never a per-key
    # ``... WHERE user_id = ? AND key = ?`` probe.
    assert not any(
        kind == "fetch_one" and "key = ?" in sql for kind, sql, _ in counted.reads
    )


def test_set_many_updates_preserve_identity_and_values(db, test_user):
    counted = _CountingDB(db)
    manager = SettingsManager(counted)
    uid = test_user.id
    first = manager.set_many(uid, {"keep": "old", "fresh": "one"})
    original = first["keep"]

    updated = manager.set_many(uid, {"keep": "new", "fresh": "two"})

    assert updated["keep"].id == original.id
    assert updated["keep"].created_at == original.created_at
    assert updated["keep"].value == "new"
    assert updated["fresh"].value == "two"
    assert manager.get_setting(uid, "keep") == "new"
    assert manager.get_settings_count(uid) == 2


def test_large_bulk_write_chunks_in_bounds(db, test_user):
    n = 1200
    counted = _CountingDB(db)
    manager = SettingsManager(counted, config=_huge_config(n))
    uid = test_user.id
    items = {f"bulk{i}": f"v{i}" for i in range(n)}

    results = manager.set_many(uid, items)

    assert set(results) == set(items)
    batched = _batched_reads(counted)
    # 1200 keys -> ceil(1200 / 500) = 3 chunked lookups + 1 COUNT read.
    assert len(batched) == 3
    assert len(counted.reads) == 4
    for _, sql, params in batched:
        # First bind is the requesting user; the IN list itself is <= 500.
        assert "user_id = ?" in sql
        assert params[0] == uid
        assert 1 <= len(params) - 1 <= 500
        assert sql.count("?") == len(params)
    assert manager.get_settings_count(uid) == n


def test_bulk_write_never_crosses_user_boundary(db, two_users):
    manager = SettingsManager(db)
    alice, bob = two_users[0].id, two_users[1].id

    alice_setting = manager.set_setting(alice, "shared", "alice-secret")
    # Bob writes the same key. The batched lookup must not see Alice's row,
    # so Bob gets a brand-new row instead of updating (or echoing) hers.
    counted = _CountingDB(db)
    bob_manager = SettingsManager(counted)
    bob_setting = bob_manager.set_many(bob, {"shared": "bob-value"})["shared"]

    assert isinstance(bob_setting, UserSetting)
    assert bob_setting.id != alice_setting.id
    assert bob_setting.value == "bob-value"
    assert manager.get_setting(alice, "shared") == "alice-secret"
    assert bob_manager.get_setting(bob, "shared") == "bob-value"

    # Every batched statement carries the caller's user id in the WHERE
    # clause: a lookup with no owner predicate would fail this check.
    batched = _batched_reads(counted)
    assert len(batched) == 1
    assert "user_id = ?" in batched[0][1]
    assert batched[0][2][0] == bob


@pytest.mark.unit
def test_bulk_key_chunks_are_bounded():
    from src.core.settings.manager import _BULK_KEY_CHUNK_SIZE

    assert _BULK_KEY_CHUNK_SIZE == 500
