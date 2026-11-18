"""Multi-worker (``docker compose up --scale backend=N``) ID-generation regressions.

Two independent snowflake generators existed with a hardcoded worker/machine
id of ``1``. With N replicas that meant every worker produced the *same* ID
for a given millisecond/sequence, colliding on primary keys. These tests pin
the per-replica identity derivation so scaled workers stay collision-free.
"""

import socket

import pytest

from src.utils.encryption.core.snowflake import SnowflakeGenerator
from src.bootstrap.utilities import _resolve_snowflake_id
from src.core.messaging.services import base as messaging_base


def _clear_node_env(monkeypatch):
    for name in (
        "PLEXICHAT_WORKER_ID",
        "PLEXICHAT_DATACENTER_ID",
        "PLEXICHAT_MACHINE_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_snowflake_auto_ids_are_unique_per_replica(monkeypatch):
    """Two replicas with different hostnames must get different (dc, worker) pairs.

    Even generating in the *same millisecond* with the same sequence, their
    IDs must differ — this is the exact collision the old hardcoded ``1``
    caused under ``--scale backend=4``.
    """
    _clear_node_env(monkeypatch)

    monkeypatch.setattr(socket, "gethostname", lambda: "backend-1")
    worker_a = SnowflakeGenerator()
    monkeypatch.setattr(socket, "gethostname", lambda: "backend-2")
    worker_b = SnowflakeGenerator()

    assert (worker_a.datacenter_id, worker_a.worker_id) != (
        worker_b.datacenter_id,
        worker_b.worker_id,
    )

    fixed_ms = 1_700_000_000_000
    monkeypatch.setattr(worker_a, "_get_timestamp", lambda: fixed_ms)
    monkeypatch.setattr(worker_b, "_get_timestamp", lambda: fixed_ms)

    id_a, id_b = worker_a.generate(), worker_b.generate()
    assert id_a != id_b
    parsed_a, parsed_b = worker_a.parse(id_a), worker_b.parse(id_b)
    assert parsed_a["worker_id"] == worker_a.worker_id
    assert parsed_b["worker_id"] == worker_b.worker_id


def test_snowflake_env_overrides_still_pinned(monkeypatch):
    """Explicit per-worker env ids must win over hostname derivation."""
    monkeypatch.setattr(socket, "gethostname", lambda: "backend-1")
    monkeypatch.setenv("PLEXICHAT_WORKER_ID", "3")
    monkeypatch.setenv("PLEXICHAT_DATACENTER_ID", "7")

    generator = SnowflakeGenerator()
    assert generator.worker_id == 3
    assert generator.datacenter_id == 7
    parsed = generator.parse(generator.generate())
    assert parsed["worker_id"] == 3
    assert parsed["datacenter_id"] == 7


@pytest.mark.parametrize(
    "configured,expected",
    [
        (None, None),  # unset -> auto-derive (the bug fix)
        (0, 0),  # explicit zero is a valid id, not falsy
        (5, 5),
        ("9", 9),  # config may deliver numeric strings
    ],
)
def test_bootstrap_snowflake_id_resolution(configured, expected):
    """Bootstrap must forward ``None`` instead of coercing it to ``1``."""
    assert _resolve_snowflake_id(configured) == expected


def test_messaging_machine_id_is_unique_per_replica(monkeypatch):
    """The base messaging snowflake generator derives a per-replica machine id.

    It must no longer default every replica to machine id ``1``, otherwise
    message/conversation IDs collide across scaled workers.
    """
    _clear_node_env(monkeypatch)
    monkeypatch.setattr(messaging_base.config, "get", lambda *a, **k: {})

    monkeypatch.setattr(socket, "gethostname", lambda: "backend-1")
    service_a = messaging_base.BaseService(db=None)
    monkeypatch.setattr(socket, "gethostname", lambda: "backend-2")
    service_b = messaging_base.BaseService(db=None)

    assert service_a._machine_id != service_b._machine_id

    fixed_ms = 1_700_000_000_000
    monkeypatch.setattr(service_a, "_get_timestamp", lambda: fixed_ms)
    monkeypatch.setattr(service_b, "_get_timestamp", lambda: fixed_ms)
    assert service_a._generate_id() != service_b._generate_id()


def test_messaging_machine_id_env_override(monkeypatch):
    """An explicit PLEXICHAT_MACHINE_ID pins the messaging generator."""
    _clear_node_env(monkeypatch)
    monkeypatch.setattr(messaging_base.config, "get", lambda *a, **k: {})
    monkeypatch.setenv("PLEXICHAT_MACHINE_ID", "42")
    service = messaging_base.BaseService(db=None)
    assert service._machine_id == 42
