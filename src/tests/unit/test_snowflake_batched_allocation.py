"""Regression coverage for batched Snowflake sequence allocation.

The hot ID path used to acquire a process-wide lock for *every* ID and
re-derive the hostname/PID identity for every generator construction. These
tests pin the replacement: sequences are reserved in disjoint per-thread
batches (so the lock is no longer a per-ID bottleneck), uniqueness across
workers and concurrent callers is preserved, the backwards-clock guard still
raises before mutating state, and the identity derivation is cached.
"""

import socket
import threading
import types

import pytest

from src.core.messaging.services import base as messaging_base
from src.utils.encryption.core import snowflake as snowflake_mod
from src.utils.encryption.core.snowflake import SnowflakeGenerator


def _count_lock_acquisitions(obj, lock_attr, stats):
    """Wrap ``obj.<lock_attr>`` so each ``with`` block is counted."""
    real = getattr(obj, lock_attr)

    class CountingLock:
        def __enter__(self):
            stats["n"] += 1
            return real.__enter__()

        def __exit__(self, *exc):
            return real.__exit__(*exc)

    setattr(obj, lock_attr, CountingLock())


def _install_counting_sha256(module, calls, monkeypatch):
    real_sha256 = module.hashlib.sha256

    def counting_sha256(data):
        calls["n"] += 1
        return real_sha256(data)

    monkeypatch.setattr(
        module, "hashlib", types.SimpleNamespace(sha256=counting_sha256)
    )


def test_batched_allocation_removes_per_id_lock_acquisition():
    """One lock acquisition now covers a whole batch, not a single ID."""
    gen = SnowflakeGenerator(worker_id=1, datacenter_id=1)
    stats = {"n": 0}
    _count_lock_acquisitions(gen, "_lock", stats)

    total = 4000
    ids = [gen.generate() for _ in range(total)]

    assert len(set(ids)) == total
    # Before batching this was exactly ``total`` (one acquire per ID).
    assert stats["n"] < total // 8, stats["n"]


def test_messaging_batched_allocation_removes_per_id_lock_acquisition(monkeypatch):
    monkeypatch.delenv("PLEXICHAT_MACHINE_ID", raising=False)
    monkeypatch.setattr(messaging_base, "_coordinated_machine_id", None)
    monkeypatch.setattr(messaging_base.config, "get", lambda *a, **k: {})
    service = messaging_base.BaseService(db=None)
    stats = {"n": 0}
    _count_lock_acquisitions(service, "_id_lock", stats)

    total = 4000
    ids = [service._generate_id() for _ in range(total)]

    assert len(set(ids)) == total
    assert stats["n"] < total // 8, stats["n"]


def test_multiworker_batched_ids_are_unique_under_concurrency(monkeypatch):
    """Distinct workers serving batches concurrently in one ms cannot collide."""
    worker_count = 8
    threads_per_worker = 4
    per_thread = 128  # 512 per worker — well under the 4096 sequence space
    fixed_ms = 1_700_000_000_000

    workers = []
    for worker_id in range(worker_count):
        gen = SnowflakeGenerator(worker_id=worker_id, datacenter_id=0)
        monkeypatch.setattr(gen, "_get_timestamp", lambda: fixed_ms)
        workers.append(gen)

    all_ids = []
    collected_lock = threading.Lock()
    errors = []

    def emit(gen):
        try:
            local = [gen.generate() for _ in range(per_thread)]
            with collected_lock:
                all_ids.extend(local)
        except Exception as exc:  # surface worker failures in the assertions
            errors.append(exc)

    threads = [
        threading.Thread(target=emit, args=(workers[i % worker_count],))
        for i in range(worker_count * threads_per_worker)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(all_ids) == worker_count * threads_per_worker * per_thread
    assert len(set(all_ids)) == len(all_ids)


def test_clock_rollback_still_raises_mid_batch(monkeypatch):
    """A backwards clock step raises even with sequences left in the batch."""
    gen = SnowflakeGenerator(worker_id=1, datacenter_id=1)
    fixed_ms = 1_700_000_000_000
    clock = {"now": fixed_ms}
    monkeypatch.setattr(gen, "_get_timestamp", lambda: clock["now"])

    first = gen.generate()
    second = gen.generate()  # served from the reserved batch
    assert gen.parse(second)["sequence"] == gen.parse(first)["sequence"] + 1

    cursor_before, ts_before = gen._reserve_cursor, gen.last_timestamp

    clock["now"] = fixed_ms - 1000
    with pytest.raises(RuntimeError, match="Clock moved backwards"):
        gen.generate()
    # A rejected call must not touch reservation state.
    assert gen._reserve_cursor == cursor_before
    assert gen.last_timestamp == ts_before

    clock["now"] = fixed_ms
    recovered = gen.generate()
    assert recovered not in (first, second)
    assert gen.parse(recovered)["sequence"] == gen.parse(second)["sequence"] + 1


def test_node_identity_derivation_is_cached(monkeypatch):
    calls = {"n": 0}
    _install_counting_sha256(snowflake_mod, calls, monkeypatch)
    snowflake_mod._node_id_from_host.cache_clear()

    for name in ("PLEXICHAT_WORKER_ID", "PLEXICHAT_DATACENTER_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "stable-host")

    generators = [SnowflakeGenerator() for _ in range(6)]

    # One digest for the shared (hostname, pid) pair, not two per generator.
    assert calls["n"] == 1
    assert len({(g.worker_id, g.datacenter_id) for g in generators}) == 1


def test_messaging_machine_id_derivation_is_cached(monkeypatch):
    calls = {"n": 0}
    _install_counting_sha256(messaging_base, calls, monkeypatch)
    messaging_base._machine_id_from_host.cache_clear()

    monkeypatch.delenv("PLEXICHAT_MACHINE_ID", raising=False)
    monkeypatch.setattr(messaging_base, "_coordinated_machine_id", None)
    monkeypatch.setattr(messaging_base.config, "get", lambda *a, **k: {})
    monkeypatch.setattr(socket, "gethostname", lambda: "stable-host")

    services = [messaging_base.BaseService(db=None) for _ in range(6)]

    assert calls["n"] == 1
    assert len({service._machine_id for service in services}) == 1
