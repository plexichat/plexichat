"""Scaled-worker Snowflake coordination regressions.

``docker compose up --scale backend=N`` gives every replica the same image and
environment, so a per-replica worker id cannot be supplied by compose env.
These tests simulate N workers sharing one Valkey instance and pin the
guarantees the hostname/PID derivation alone could not provide:

* every live worker claims a distinct node id (atomic SET NX lease);
* even generating in the *same millisecond* with the same sequence, two
  workers cannot mint the same ID;
* the old ``gethostname()`` failure path no longer yields a constant id.
"""

import socket

import pytest

from src.core.database.valkey_node_id import (
    NODE_ID_SPACE,
    claim_node_id,
)
from src.core.messaging.services import base as messaging_base
from src.utils.encryption.core.snowflake import SnowflakeGenerator


class FakeValkey:
    """In-memory stand-in for the subset of ValkeyClient the lease uses."""

    def __init__(self):
        self.store = {}

    def set_nx(self, key, value, ttl=None):
        if key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def expire(self, key, ttl):
        return key in self.store

    def delete(self, key):
        return self.store.pop(key, None) is not None


def _node_parts(node_id):
    return node_id & 0x1F, (node_id >> 5) & 0x1F


def test_claims_unique_node_ids_for_every_replica():
    valkey = FakeValkey()
    leases = [claim_node_id(valkey, start_refresh=False) for _ in range(NODE_ID_SPACE)]
    assert len({lease.node_id for lease in leases}) == NODE_ID_SPACE
    for lease in leases:
        assert valkey.get(lease.key) == lease.token


def test_scaled_workers_never_generate_colliding_ids(monkeypatch):
    """N workers claiming from shared Valkey cannot collide, even in one ms."""
    valkey = FakeValkey()
    fixed_ms = 1_700_000_000_000
    ids = []

    for _ in range(32):  # a scale level where a 10-bit hash collides ~40%
        lease = claim_node_id(valkey, start_refresh=False)
        worker_id, datacenter_id = _node_parts(lease.node_id)
        generator = SnowflakeGenerator(worker_id=worker_id, datacenter_id=datacenter_id)
        monkeypatch.setattr(generator, "_get_timestamp", lambda: fixed_ms)
        ids.append(generator.generate())

    assert len(set(ids)) == 32


def test_rebind_node_id_changes_generated_ids():
    """A coordinator can rebind a generator before any IDs are served."""
    generator = SnowflakeGenerator(worker_id=1, datacenter_id=1)
    before = generator.parse(generator.generate())

    worker_id, datacenter_id = _node_parts(733)
    generator.rebind_node_id(worker_id, datacenter_id)
    after = generator.parse(generator.generate())

    assert before["worker_id"] == 1 and before["datacenter_id"] == 1
    assert after["worker_id"] == worker_id
    assert after["datacenter_id"] == datacenter_id


def test_lease_exhaustion_and_reclaim():
    valkey = FakeValkey()
    leases = [
        claim_node_id(valkey, start=0, start_refresh=False)
        for _ in range(NODE_ID_SPACE)
    ]
    assert {lease.node_id for lease in leases} == set(range(NODE_ID_SPACE))
    with pytest.raises(RuntimeError, match="No free Snowflake node id"):
        claim_node_id(valkey, start=0, start_refresh=False)

    freed = leases[0]
    freed.release()
    assert valkey.get(freed.key) is None
    reclaimed = claim_node_id(valkey, start=0, start_refresh=False)
    assert reclaimed.node_id == freed.node_id


def test_gethostname_failure_does_not_yield_constant_id(monkeypatch):
    """A failed hostname lookup must not collapse every worker to one id."""

    def boom():
        raise OSError("no hostname")

    monkeypatch.setattr(socket, "gethostname", boom)
    monkeypatch.delenv("PLEXICHAT_WORKER_ID", raising=False)
    monkeypatch.delenv("PLEXICHAT_DATACENTER_ID", raising=False)
    # Same PID on purpose: only a per-process random suffix can separate them.
    monkeypatch.setattr("os.getpid", lambda: 1)

    a = SnowflakeGenerator()
    b = SnowflakeGenerator()
    assert (a.datacenter_id, a.worker_id) != (b.datacenter_id, b.worker_id)


def test_startup_wiring_rebinds_both_generators(monkeypatch):
    """The Valkey lease from startup is applied to every ID generator."""
    from src.server import initializer
    from src.utils import encryption

    monkeypatch.setattr(encryption, "snowflake_node_id_is_explicit", lambda: False)
    monkeypatch.delenv("PLEXICHAT_MACHINE_ID", raising=False)
    applied = {}
    monkeypatch.setattr(
        encryption, "set_snowflake_node_id", lambda n: applied.setdefault("enc", n)
    )
    monkeypatch.setattr(
        messaging_base, "set_machine_id", lambda n: applied.setdefault("msg", n)
    )

    initializer._coordinate_snowflake_node_id(FakeValkey())

    lease = initializer._snowflake_node_lease
    assert lease is not None
    assert applied == {"enc": lease.node_id, "msg": lease.node_id}


def test_startup_wiring_respects_explicit_ids(monkeypatch):
    """Explicitly pinned ids are never overwritten by coordination."""
    from src.server import initializer
    from src.utils import encryption

    monkeypatch.setattr(encryption, "snowflake_node_id_is_explicit", lambda: True)
    called = []
    monkeypatch.setattr(encryption, "set_snowflake_node_id", lambda n: called.append(n))

    valkey = FakeValkey()
    initializer._coordinate_snowflake_node_id(valkey)

    assert called == []
    assert valkey.store == {}


def test_messaging_generator_uses_coordinated_machine_id(monkeypatch):
    """BaseService instances honour the node id claimed from Valkey."""
    monkeypatch.setattr(messaging_base.config, "get", lambda *a, **k: {})
    monkeypatch.delenv("PLEXICHAT_MACHINE_ID", raising=False)
    monkeypatch.setattr(messaging_base, "_coordinated_machine_id", None)

    valkey = FakeValkey()
    lease = claim_node_id(valkey, start_refresh=False)
    messaging_base.set_machine_id(lease.node_id)

    service = messaging_base.BaseService(db=None)
    assert service._machine_id == lease.node_id
