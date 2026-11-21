"""PERF regression: the device-trust overview must not fan out per user.

Before this change ``get_device_trust_overview`` enumerated up to
``MAX_OVERVIEW_USERS`` ids and then called ``get_devices(uid)`` and
``get_sessions(uid)`` once per id: 2N sequential DB round trips for a single
admin poll (up to ~2000 queries). It now prefers the manager's bulk accessors,
which read each row set in bounded IN chunks.

Safety properties pinned here:

* the bulk accessors are *live DB reads* -- no process-local cache -- so a
  trust/revoke on another worker is visible on the very next call;
* rows are grouped by the ``user_id`` they carry and checked against the
  explicit requested set, both in the manager and again in the overview, so a
  row for an unrequested user can never leak into another user's group;
* if no bulk accessor exists or it fails, the original per-user path is used,
  so an unavailable optimization never changes what the admin sees.
"""

from types import SimpleNamespace

from src.api.routes.admin.device_overview import (
    MAX_OVERVIEW_USERS,
    get_device_trust_overview,
)
from src.core.auth.managers.device import DeviceMixin, _BULK_IN_CHUNK
from src.core.auth.managers.session.lifecycle import (
    LifecycleMixin,
    _SESSION_BULK_IN_CHUNK,
)


USER_IDS = [101, 102, 103, 104]


def _device(uid, did, trusted=False):
    return SimpleNamespace(
        id=did,
        user_id=uid,
        trusted=trusted,
        trusted_at=100 if trusted else None,
        name=f"dev-{did}",
        device_type="desktop",
        first_seen_at=1,
        last_seen_at=2,
    )


def _session(uid, sid, device_id=None, expires_at=10_000):
    return SimpleNamespace(
        id=sid,
        user_id=uid,
        device_id=device_id,
        expires_at=expires_at,
        created_at=1,
        last_activity=2,
    )


class _BulkManager:
    """Manager exposing the bulk API; counts both bulk and legacy calls."""

    def __init__(self, user_ids):
        self._db = SimpleNamespace(fetch_all=lambda *a, **k: [])
        self._config = {"sessions": {"expire_hours": 24}}
        self.bulk_calls = 0
        self.legacy_calls = 0
        self._devices = {
            uid: [_device(uid, uid * 10, trusted=(uid == 101))] for uid in user_ids
        }
        self._sessions = {
            uid: [_session(uid, uid * 100, device_id=uid * 10)] for uid in user_ids
        }

    def _get_timestamp(self):
        return 1000

    def get_devices_bulk(self, ids):
        self.bulk_calls += 1
        return {uid: list(rows) for uid, rows in self._devices.items() if uid in ids}

    def get_sessions_bulk(self, ids):
        self.bulk_calls += 1
        return {uid: list(rows) for uid, rows in self._sessions.items() if uid in ids}

    def get_devices(self, uid):
        self.legacy_calls += 1
        return []

    def get_sessions(self, uid):
        self.legacy_calls += 1
        return []


class _LegacyManager(_BulkManager):
    """The pre-optimization manager: per-user accessors only."""

    get_devices_bulk = None
    get_sessions_bulk = None

    def get_devices(self, uid):
        self.legacy_calls += 1
        return self._devices.get(uid, [])

    def get_sessions(self, uid):
        self.legacy_calls += 1
        return self._sessions.get(uid, [])


def _enumerate(*uids):
    class _DB:
        def fetch_all(self, query, params=None):
            return [{"user_id": uid} for uid in uids]

    return _DB()


class TestOverviewUsesBulkReads:
    def test_bulk_path_replaces_per_user_fanout(self):
        """After: two bulk reads, zero per-user calls, identical data."""
        mgr = _BulkManager(USER_IDS)
        mgr._db = _enumerate(*USER_IDS)
        overview = get_device_trust_overview(mgr, mgr._config)

        assert mgr.bulk_calls == 2  # devices + sessions, independent of N
        assert mgr.legacy_calls == 0
        assert overview["totals"]["users"] == len(USER_IDS)
        assert overview["totals"]["devices"] == len(USER_IDS)
        assert overview["totals"]["sessions"] == len(USER_IDS)
        trusted = next(u for u in overview["users"] if u["user_id"] == "101")
        assert trusted["trusted_device_count"] == 1
        assert trusted["sessions"][0]["policy_kind"] == "trusted"

    def test_legacy_path_is_the_before_baseline_and_matches(self):
        """Before: 2N per-user reads; both paths produce the same payload."""
        before_mgr = _LegacyManager(USER_IDS)
        before_mgr._db = _enumerate(*USER_IDS)
        before = get_device_trust_overview(before_mgr, before_mgr._config)
        assert before_mgr.legacy_calls == 2 * len(USER_IDS)
        assert before_mgr.bulk_calls == 0

        after_mgr = _BulkManager(USER_IDS)
        after_mgr._db = _enumerate(*USER_IDS)
        after = get_device_trust_overview(after_mgr, after_mgr._config)
        assert after == before

    def test_bulk_failure_falls_back_without_dropping_rows(self):
        """A broken bulk accessor must not silently change admin data."""
        mgr = _BulkManager(USER_IDS)
        mgr._db = _enumerate(*USER_IDS)

        def _boom(ids):
            raise RuntimeError("valkey/DB hiccup")

        mgr.get_devices_bulk = _boom
        mgr.get_sessions_bulk = _boom
        # Feed the legacy path real rows so we can assert equivalence.
        original_get_devices = mgr.get_devices
        original_get_sessions = mgr.get_sessions

        def _legacy_devices(uid):
            mgr.legacy_calls += 1
            return mgr._devices.get(uid, [])

        def _legacy_sessions(uid):
            mgr.legacy_calls += 1
            return mgr._sessions.get(uid, [])

        mgr.get_devices = _legacy_devices
        mgr.get_sessions = _legacy_sessions
        overview = get_device_trust_overview(mgr, mgr._config)
        assert overview["totals"]["devices"] == len(USER_IDS)
        assert mgr.legacy_calls == 2 * len(USER_IDS)
        assert original_get_devices is not None and original_get_sessions is not None


class TestFailClosedGrouping:
    def test_extra_bulk_keys_are_ignored(self):
        """A bulk result carrying another user's key cannot reach the payload."""
        mgr = _BulkManager(USER_IDS)
        mgr._db = _enumerate(*USER_IDS)
        mgr._devices[9999] = [_device(9999, 99990)]
        mgr._sessions[9999] = [_session(9999, 999900)]

        overview = get_device_trust_overview(mgr, mgr._config)
        rendered = str(overview)
        assert "99990" not in rendered
        assert "999900" not in rendered
        assert all(u["user_id"] != "9999" for u in overview["users"])

    def test_manager_drops_rows_for_unrequested_users(self):
        """Device/session bulk accessors group strictly by requested ids."""
        stored = [
            {
                "id": 1,
                "user_id": 101,
                "fingerprint": "fp",
                "fingerprint_encrypted": None,
                "name": "a",
                "name_encrypted": None,
                "device_type": "desktop",
                "device_type_encrypted": None,
                "trusted": 1,
                "trusted_at": 5,
                "first_seen_at": 1,
                "last_seen_at": 2,
            },
            {
                "id": 2,
                "user_id": 9999,  # not requested
                "fingerprint": "fp2",
                "fingerprint_encrypted": None,
                "name": "b",
                "name_encrypted": None,
                "device_type": "phone",
                "device_type_encrypted": None,
                "trusted": 0,
                "trusted_at": None,
                "first_seen_at": 1,
                "last_seen_at": 2,
            },
        ]

        class _DB:
            def fetch_all(self, query, params=None):
                return list(stored)

        class _DeviceReader(DeviceMixin):
            _db = _DB()
            crypto = None

        grouped = _DeviceReader().get_devices_bulk([101, 102])
        assert set(grouped) == {101, 102}
        assert [d.id for d in grouped[101]] == [1]
        assert grouped[102] == []

    def test_session_bulk_keeps_revoked_predicate(self):
        """Batched session reads still exclude revoked sessions."""
        seen = []

        class _DB:
            def fetch_all(self, query, params=None):
                seen.append(query)
                return []

        class _SessionReader(LifecycleMixin):
            _db = _DB()
            crypto = None

        _SessionReader().get_sessions_bulk([1, 2])
        assert len(seen) == 1
        assert "revoked = 0" in seen[0]


class TestRealManagerBulkParity:
    def test_bulk_matches_single_user_reads(self, db, auth_manager):
        """The batched accessors return exactly what the old per-user path did."""
        from unittest.mock import patch

        from src.utils import encryption

        ids = []
        for name in ("ov_batch_a", "ov_batch_b", "ov_batch_c"):
            with patch.object(encryption, "hash_password", return_value="fake_hash_$t"):
                user = auth_manager.register(
                    username=name,
                    email=f"{name}@example.com",
                    password="TestPass123!",
                )
            with patch.object(encryption, "verify_password", return_value=True):
                auth_manager.login(
                    name, "TestPass123!", device_info={"fingerprint": f"{name}_fp"}
                )
            ids.append(user.id)
        auth_manager.trust_device(ids[0], "ov_batch_a_fp")

        def _instance():
            from src.core.auth._lazy import _get_auth_manager

            return _get_auth_manager().get_instance()

        mgr = _instance()
        assert callable(getattr(mgr, "get_devices_bulk", None))
        assert callable(getattr(mgr, "get_sessions_bulk", None))

        devices = mgr.get_devices_bulk(ids)
        sessions = mgr.get_sessions_bulk(ids)
        assert devices == {uid: mgr.get_devices(uid) for uid in ids}
        assert sessions == {uid: mgr.get_sessions(uid) for uid in ids}
        assert sum(len(v) for v in devices.values()) >= len(ids)
        assert sum(len(v) for v in sessions.values()) >= len(ids)

        overview = get_device_trust_overview(mgr, mgr._config)
        entry = next(u for u in overview["users"] if u["user_id"] == str(ids[0]))
        assert entry["trusted_device_count"] >= 1


class TestBoundedChunking:
    def test_bulk_reads_use_bounded_in_chunks(self):
        """Query count and bind count stay bounded for a full overview page."""
        seen = []

        class _DB:
            def fetch_all(self, query, params=None):
                seen.append((query, params))
                return []

        class _Reader(DeviceMixin, LifecycleMixin):
            _db = _DB()
            crypto = None

        reader = _Reader()
        ids = list(range(1, MAX_OVERVIEW_USERS + 1))
        reader.get_devices_bulk(ids)
        assert len(seen) == (MAX_OVERVIEW_USERS + _BULK_IN_CHUNK - 1) // _BULK_IN_CHUNK
        assert all(len(params) <= _BULK_IN_CHUNK for _, params in seen)
        assert all(_BULK_IN_CHUNK <= 999 for _ in [_BULK_IN_CHUNK])

        seen.clear()
        reader.get_sessions_bulk(ids)
        assert (
            len(seen)
            == (MAX_OVERVIEW_USERS + _SESSION_BULK_IN_CHUNK - 1)
            // _SESSION_BULK_IN_CHUNK
        )
        assert all(len(params) <= _SESSION_BULK_IN_CHUNK for _, params in seen)
