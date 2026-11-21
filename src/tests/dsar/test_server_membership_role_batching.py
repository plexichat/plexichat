"""DSAR server-membership collection must not issue one role query per server.

``ServersCollector._collect_memberships`` used to call a per-member
``srv_member_roles`` lookup inside the membership loop, so a subject who had
joined N servers produced N+1 queries in this single category. Role ids are now
gathered with one subject-scoped join and grouped in memory, keeping the exact
same payload while capping the query budget at a constant.
"""

import time
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def dsar_db(db):
    from src.core.dsar import setup as dsar_setup

    dsar_setup(db)
    return db


def _clear(db):
    from src.core.database.cache.base import mem_cache_clear_pattern

    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


def _seed_memberships(db, user_id, start, count):
    """Create ``count`` memberships (2 roles each) for ``user_id``.

    Ids are derived from a unique base so repeated calls in one test never
    collide. Returns a list of (member_id, [role_id, ...]) expectations.
    """
    now = int(time.time())
    base = 800_000_000 + (int(uuid.uuid4().int) % 1_000_000) * 1000 + start
    expected = []
    for i in range(count):
        server_id = base + i
        member_id = server_id * 100
        role_a, role_b = server_id * 10, server_id * 10 + 1
        db.execute(
            "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (server_id, f"s-{server_id}", user_id, now, now),
        )
        for role_id in (role_a, role_b):
            db.execute(
                "INSERT INTO srv_roles (id, server_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (role_id, server_id, f"r-{role_id}", now, now),
            )
        db.execute(
            "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (member_id, server_id, user_id, now, now),
        )
        for role_id in (role_a, role_b):
            db.execute(
                "INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at) "
                "VALUES (?, ?, ?, ?)",
                (server_id * 1000 + role_id, member_id, role_id, now),
            )
        expected.append((member_id, [role_a, role_b]))
    return expected


def _collect_and_count(db, user_id):
    from src.core.dsar.collectors.servers import ServersCollector

    _clear(db)
    memberships = ServersCollector(db)._collect_memberships(user_id)
    return memberships, db.get_request_metrics()["query_count"]


def test_member_role_queries_are_batched(dsar_db, auth_manager, pii_gen):
    """Role lookup is one scoped query regardless of membership count."""
    from unittest.mock import patch

    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"rolebatch_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    db = dsar_db

    small_expected = _seed_memberships(db, user.id, 0, 3)
    _small_memberships, small_count = _collect_and_count(db, user.id)

    large_expected = _seed_memberships(db, user.id, 100, 30)
    large_memberships, large_count = _collect_and_count(db, user.id)

    # Before batching: 3 memberships -> 4 queries, 30 -> 31 queries.
    # After batching: both are 2 (memberships + one role join).
    assert large_count <= 4, (
        "server-membership collection must not issue one role query per "
        f"membership: 30 memberships issued {large_count} queries"
    )
    assert large_count <= small_count + 1, (
        "query count must not grow with the number of memberships: "
        f"3 -> {small_count}, 30 -> {large_count}"
    )

    # Payload is unchanged: every membership carries exactly the role ids of
    # its own rows (order-preserving), and no decoy role leaks in.
    by_id = {m["id"]: m["role_ids"] for m in large_memberships}
    for member_id, role_ids in small_expected + large_expected:
        assert by_id.get(member_id) == role_ids
    all_seen = {rid for ids in by_id.values() for rid in ids}
    assert 777777 not in all_seen


def test_other_subject_roles_never_leak(dsar_db, auth_manager, pii_gen):
    """The role join is anchored on the subject, not on bare member ids."""
    from unittest.mock import patch

    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        subject = auth_manager.register(
            username=f"roleleak_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
        stranger = auth_manager.register(
            username=f"roleleak_other_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    db = dsar_db
    now = int(time.time())
    base = 700_000_000 + (int(uuid.uuid4().int) % 1_000_000) * 1000
    subject_member = base * 100
    stranger_member = (base + 1) * 100
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (base, "mine", subject.id, now, now),
    )
    db.execute(
        "INSERT INTO srv_roles (id, server_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (111111, base, "mine-role", now, now),
    )
    db.execute(
        "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (subject_member, base, subject.id, now, now),
    )
    db.execute(
        "INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at) "
        "VALUES (?, ?, ?, ?)",
        (base * 1000 + 1, subject_member, 111111, now),
    )
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (base + 1, "theirs", stranger.id, now, now),
    )
    db.execute(
        "INSERT INTO srv_roles (id, server_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (222222, base + 1, "theirs-role", now, now),
    )
    db.execute(
        "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (stranger_member, base + 1, stranger.id, now, now),
    )
    db.execute(
        "INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at) "
        "VALUES (?, ?, ?, ?)",
        ((base + 1) * 1000 + 2, stranger_member, 222222, now),
    )

    memberships, _count = _collect_and_count(db, subject.id)
    assert len(memberships) == 1
    assert memberships[0]["id"] == subject_member
    assert memberships[0]["role_ids"] == [111111]
    assert 222222 not in memberships[0]["role_ids"]
