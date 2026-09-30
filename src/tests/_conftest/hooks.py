"""pytest_configure / pytest_collection_modifyitems hooks."""

import os
import random

import pytest


# =============================================================================
# Pytest Configuration
# =============================================================================


def pytest_configure(config):
    """Register custom markers."""
    # Under pytest-xdist every worker shares the single on-disk Hypothesis
    # database; concurrent writes from parallel workers can raise database-
    # lock errors that surface as random, non-reproducible failures on
    # arbitrary tests. Give each worker an in-memory database instead.
    if os.environ.get("PYTEST_XDIST_WORKER"):
        try:
            from hypothesis import settings

            settings.register_profile("xdist", settings(database=None))
            settings.load_profile("xdist")
        except Exception:
            pass
    config.addinivalue_line("markers", "unit: Fast unit tests, no database required")
    config.addinivalue_line("markers", "integration: Tests requiring full module setup")
    config.addinivalue_line(
        "markers", "slow: Intentionally slow tests (rate limiting, timeouts)"
    )
    config.addinivalue_line(
        "markers", "security: Security-critical test that must not fail"
    )
    config.addinivalue_line("markers", "auth: Authentication module tests")
    config.addinivalue_line("markers", "messaging: Messaging module tests")
    config.addinivalue_line("markers", "servers: Server module tests")
    config.addinivalue_line("markers", "presence: Presence module tests")
    config.addinivalue_line("markers", "relationships: Relationships module tests")
    config.addinivalue_line("markers", "reactions: Reactions module tests")
    config.addinivalue_line("markers", "webhooks: Webhooks module tests")
    config.addinivalue_line("markers", "threads: Threads module tests")
    config.addinivalue_line("markers", "notifications: Notifications module tests")
    config.addinivalue_line("markers", "ratelimit: Rate limiting tests")
    config.addinivalue_line("markers", "api: API route tests")
    config.addinivalue_line("markers", "media: Media module tests")
    config.addinivalue_line("markers", "search: Search module tests")
    config.addinivalue_line("markers", "applications: Applications module tests")
    config.addinivalue_line("markers", "stickers: Stickers module tests")
    config.addinivalue_line("markers", "polls: Polls module tests")
    config.addinivalue_line("markers", "soundboard: Soundboard module tests")
    config.addinivalue_line("markers", "settings: User settings module tests")
    config.addinivalue_line("markers", "voice: Voice module tests")
    config.addinivalue_line("markers", "websocket: WebSocket module tests")
    config.addinivalue_line("markers", "encryption: Encryption module tests")
    config.addinivalue_line("markers", "embeds: Embeds module tests")
    config.addinivalue_line("markers", "automod: Auto-moderation module tests")
    config.addinivalue_line("markers", "performance: Performance and load tests")
    config.addinivalue_line(
        "markers", "production_simulation: Production simulation tests"
    )
    config.addinivalue_line("markers", "multiprocess: Multiprocess tests")
    config.addinivalue_line("markers", "requires_postgres: Tests requiring PostgreSQL")
    config.addinivalue_line(
        "markers", "needs_valkey: Tests that require a live Valkey instance"
    )
    config.addinivalue_line(
        "markers", "crypto_heavy: Tests that use real Argon2/HKDF/chain hashing"
    )
    config.addinivalue_line(
        "markers", "migrations: Tests for the migration runner and trackers"
    )
    config.addinivalue_line(
        "markers", "database_integration: Tests requiring a real database backend"
    )
    config.addinivalue_line("markers", "benchmark: Performance benchmark tests")
    config.addinivalue_line("markers", "postgres: Tests requiring PostgreSQL/Docker")


def pytest_sessionfinish(session, exitstatus):
    """Stop the process-wide ``Database`` monitor thread at session end.

    The ``Database`` singleton auto-starts a background pool-monitor thread
    that logs periodically. Without an explicit stop it outlives pytest's
    capture/teardown and writes to already-closed streams, surfacing as
    ``ValueError: I/O operation on closed file`` "Logging error" noise
    after the final test. Best-effort: never fail the session on teardown.
    """
    try:
        from src.core.database import Database

        inst = getattr(Database, "_instance", None)
        if inst is not None:
            try:
                inst.stop_pool_monitoring()
            except Exception:
                pass
    except Exception:
        pass


def _item_file_key(item):
    """Return the per-file key used to group items for shuffling."""
    path = getattr(item, "path", None)
    if path is not None:
        return str(path)
    return str(item.fspath)


def shuffle_test_order(items, seed):
    """Reorder ``items`` by shuffling whole files, deterministically by ``seed``.

    Tests from the same file keep their relative order and stay adjacent, so
    ``pytest-xdist``'s ``--dist loadfile`` still hands each file to one worker;
    only the order of the file groups changes. The same seed always yields the
    same order, so an order-dependent failure is reproducible with
    ``--shuffle-seed``.
    """
    groups = {}
    keys = []
    for item in items:
        key = _item_file_key(item)
        if key not in groups:
            groups[key] = []
            keys.append(key)
        groups[key].append(item)
    random.Random(seed).shuffle(keys)
    return [item for key in keys for item in groups[key]]


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests based on their location, then optionally shuffle."""
    for item in items:
        item_path = str(item.fspath).replace(os.sep, "/")

        if "/security/" in item_path or "test_security" in item.name.lower():
            item.add_marker(pytest.mark.security)

        if "/auth/" in item_path:
            item.add_marker(pytest.mark.auth)
            item.add_marker(pytest.mark.integration)
        elif "/messaging/" in item_path:
            item.add_marker(pytest.mark.messaging)
            item.add_marker(pytest.mark.integration)
        elif "/servers/" in item_path:
            item.add_marker(pytest.mark.servers)
            item.add_marker(pytest.mark.integration)
        elif "/presence/" in item_path:
            item.add_marker(pytest.mark.presence)
            item.add_marker(pytest.mark.integration)
        elif "/relationships/" in item_path:
            item.add_marker(pytest.mark.relationships)
            item.add_marker(pytest.mark.integration)
        elif "/reactions/" in item_path:
            item.add_marker(pytest.mark.reactions)
            item.add_marker(pytest.mark.integration)
        elif "/webhooks/" in item_path:
            item.add_marker(pytest.mark.webhooks)
            item.add_marker(pytest.mark.integration)
        elif "/threads/" in item_path:
            item.add_marker(pytest.mark.threads)
            item.add_marker(pytest.mark.integration)
        elif "/notifications/" in item_path:
            item.add_marker(pytest.mark.notifications)
            item.add_marker(pytest.mark.integration)
        elif "/ratelimit/" in item_path:
            item.add_marker(pytest.mark.ratelimit)
            item.add_marker(pytest.mark.integration)
        elif "/api/" in item_path:
            item.add_marker(pytest.mark.api)
            item.add_marker(pytest.mark.integration)
        elif "/media/" in item_path:
            item.add_marker(pytest.mark.media)
            item.add_marker(pytest.mark.integration)
        elif "/applications/" in item_path:
            item.add_marker(pytest.mark.applications)
            item.add_marker(pytest.mark.integration)
        elif "/stickers/" in item_path:
            item.add_marker(pytest.mark.stickers)
            item.add_marker(pytest.mark.integration)
        elif "/polls/" in item_path:
            item.add_marker(pytest.mark.polls)
            item.add_marker(pytest.mark.integration)
        elif "/soundboard/" in item_path:
            item.add_marker(pytest.mark.soundboard)
            item.add_marker(pytest.mark.integration)
        elif "/settings/" in item_path:
            item.add_marker(pytest.mark.settings)
            item.add_marker(pytest.mark.integration)
        elif "/voice/" in item_path:
            item.add_marker(pytest.mark.voice)
            item.add_marker(pytest.mark.integration)
        elif "/websocket/" in item_path:
            item.add_marker(pytest.mark.websocket)
            item.add_marker(pytest.mark.integration)
        elif "/encryption/" in item_path:
            item.add_marker(pytest.mark.encryption)
            item.add_marker(pytest.mark.integration)
        elif "/embeds/" in item_path:
            item.add_marker(pytest.mark.embeds)
            item.add_marker(pytest.mark.integration)
        elif "/automod/" in item_path:
            item.add_marker(pytest.mark.automod)
            item.add_marker(pytest.mark.integration)
        elif "/performance/" in item_path:
            item.add_marker(pytest.mark.performance)
            item.add_marker(pytest.mark.slow)
        elif "/unit/" in item_path:
            item.add_marker(pytest.mark.unit)

    # Optional seeded shuffle, applied after the auto-markers so the
    # reordered list keeps every marker. Grouping by file is preserved so
    # ``--dist loadfile`` can still pin each file to a single xdist worker.
    seed = config.getoption("shuffle_seed", None)
    if seed is not None:
        items[:] = shuffle_test_order(items, seed)
