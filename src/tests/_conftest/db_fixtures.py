"""Session template + function-scoped database fixtures."""

import pytest
import os
import tempfile
from unittest.mock import patch

from src.utils import encryption

import utils.config as config


# Standard per-test SQLite pool: tiny per-connection caches (the production
# defaults reserve ~32MB page cache + 256MB mmap PER connection; with
# thousands of per-test pools that fills RAM/swap) but a generous
# max_connections — API/route tests and the threaded race tests exercise
# concurrent connections (test client + app + rate limiter + ...), so a
# small max makes them exhaust the pool.
_TEST_DB_POOL_CONFIG = {
    "min_connections": 2,
    "max_connections": 16,
    "connect_timeout": 10,
    "cache_size_kb": -1024,
    "mmap_size": 1048576,
    "journal_size_limit": 1048576,
    "temp_store": "MEMORY",
}


@pytest.fixture(scope="session", autouse=True)
def setup_config(tmpdir_factory):
    """Initialize global configuration for tests (already done at import time)."""
    # Config is already setup at import time above
    # This fixture exists for backward compatibility
    yield


@pytest.fixture(autouse=True)
def _isolate_global_config(_global_singleton_guard):
    """Restore the shared config after every test.

    Several test fixtures call ``config.setup()`` with partial configs
    (e.g. only a ``database`` section), which replaces the ENTIRE global
    config and leaks to later tests/files — silently breaking suites that
    depend on other sections (auth lockout thresholds, search discovery
    limits, media compression defaults, ...). Snapshotting here keeps every
    test's config changes fully contained.

    The restore swaps the loader's config dict directly instead of doing a
    YAML round-trip through ``config.setup()`` — the full-file dump/reload
    was the dominant per-test fixture cost (thousands of tests) and the
    snapshot dict is private to this fixture, so no other code can observe
    the swap. The loader is re-fetched in teardown because a test may have
    called ``config.setup()`` and replaced the module-level instance.
    """
    import copy
    from utils.config import _config_instance as _config_loader

    snapshot = copy.deepcopy(_config_loader.config)
    snapshot_path = getattr(_config_loader, "config_path", None)
    snapshot_setup_called = config._setup_called
    try:
        yield
    finally:
        # Restore the module-level singleton identity as well as its contents.
        # A test that calls ``config.setup()`` replaces ``_config_instance``;
        # restoring only the dict would leave that replacement (and its path /
        # setup flag) in place for every later test in the worker. Put the
        # original instance back and re-apply the snapshot to it.
        config._config_instance = _config_loader
        config._setup_called = snapshot_setup_called
        if _config_loader is not None:
            _config_loader.config = snapshot
            # Keep the loader pointed at the pre-test path so stray config.set()
            # writes don't land in a test's temp file.
            if snapshot_path is not None and hasattr(_config_loader, "config_path"):
                _config_loader.config_path = snapshot_path
            _config_loader._invalidate_cache()


# =============================================================================
# Database Fixture (Function-Scoped for Isolation)
# =============================================================================


@pytest.fixture(scope="session")
def migrated_db_template():
    """Fully-migrated empty test database, built once per worker process.

    The function-scoped ``db`` fixture copies this template file instead of
    re-running the full migration chain for every test (measured ~560 ms/test
    — the suite's dominant fixture cost). Isolation is preserved because
    each test connects to its own fresh copy of the template.

    Test-friendly crypto/encryption state is also initialised here, once per
    worker, rather than per test (~175 ms/test) — no test mutates the
    encryption module singletons, so the state is identical either way.
    """
    import shutil
    from src.core.database import Database
    from src.core.migrations import run_migrations

    # Test-friendly crypto + keyring state, once per worker.
    encryption.setup(
        worker_id=1,
        argon2_time_cost=1,
        argon2_memory_cost=8,
        argon2_parallelism=1,
    )

    temp_dir = tempfile.mkdtemp(prefix="plexichat_template_")
    template_path = os.path.join(temp_dir, "template.db")

    old_db_conf = config.get("database", None)
    # Every per-test pool copies these settings from the singleton engine.
    # The production defaults reserve ~32MB page cache + 256MB mmap PER
    # connection; with thousands of per-test pools that fills RAM/swap. Use
    # tiny caches + a 1MB WAL cap instead. Keep a generous max_connections:
    # API/route tests exercise concurrent connections (test client + app +
    # rate limiter + ...), so a small max makes them exhaust the pool.
    # (Values live in _TEST_DB_POOL_CONFIG so the per-test fixture below
    # can re-apply them to the reused singleton.)
    config.set(
        "database",
        {
            "type": "sqlite",
            "path": template_path,
            "connection_pool": dict(_TEST_DB_POOL_CONFIG),
        },
    )

    # A test that ran before this session-scoped fixture (e.g. the
    # transaction tests, which call ``Database.reset()`` and re-bind the
    # singleton to their own file) may have left the singleton pointing at
    # some other database. ``Database()`` below would then return that
    # stale instance and the migrations would be applied to the WRONG file,
    # leaving ``template_path`` empty — and every later ``db`` fixture
    # copies an empty template ("no such table"). Force a rebuild so the
    # singleton is bound to the template path. Runs once per worker, so the
    # cost is negligible.
    Database.reset()

    template_db = Database()
    template_db.connect()

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        with patch.object(encryption, "verify_password", return_value=True):
            # Apply all migrations (including irreversible ones) for tests
            run_migrations(template_db)

            # Insert system user (ID 0) for system messages
            template_db.execute(
                "INSERT INTO auth_users (id, account_type, username, password_hash, permissions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (0, "system", "system", "!", "{}", 0, 0),
            )

            # Setup reports and feedback tables
            try:
                from src.core import reports, feedback

                reports.setup(template_db)
                feedback.setup(template_db)
            except Exception:
                # If reports/feedback setup fails, create minimal tables manually
                pass  # Tables will be created by migrations or not needed

            # Ensure required tables exist (workarounds for migration issues)
            template_db.execute("""
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    device_id INTEGER,
                    ip_index TEXT,
                    ip_encrypted TEXT,
                    ua_index TEXT,
                    ua_encrypted TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_activity INTEGER NOT NULL,
                    revoked INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE
                )
            """)

            template_db.execute("""
                CREATE TABLE IF NOT EXISTS auth_api_access_tokens (
                    id INTEGER PRIMARY KEY,
                    name TEXT,
                    description TEXT,
                    token_index TEXT UNIQUE NOT NULL,
                    token_encrypted TEXT NOT NULL,
                    created_by INTEGER,
                    created_at INTEGER NOT NULL,
                    first_used_at INTEGER,
                    last_used_at INTEGER,
                    last_used_ip_index TEXT,
                    last_used_ip_encrypted TEXT,
                    ua_index TEXT,
                    ua_encrypted TEXT,
                    last_used_path TEXT,
                    expires_at INTEGER,
                    scope_mode TEXT NOT NULL DEFAULT 'none',
                    use_count_total INTEGER NOT NULL DEFAULT 0,
                    revoked INTEGER DEFAULT 0,
                    revoked_at INTEGER,
                    revoked_by INTEGER
                )
            """)

            template_db.execute("""
                CREATE TABLE IF NOT EXISTS auth_ip_blacklist (
                    ip_index TEXT PRIMARY KEY,
                    ip_encrypted TEXT,
                    reason TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER
                )
            """)

            template_db.execute("""
                CREATE TABLE IF NOT EXISTS srv_servers (
                    id INTEGER PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    icon TEXT,
                    banner TEXT,
                    invite_code TEXT UNIQUE,
                    region TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    member_count INTEGER DEFAULT 0,
                    max_members INTEGER DEFAULT 100,
                    is_nsfw INTEGER DEFAULT 0,
                    is_public INTEGER DEFAULT 1,
                    verification_level INTEGER DEFAULT 0,
                    default_channel_id INTEGER,
                    FOREIGN KEY (owner_id) REFERENCES auth_users(id) ON DELETE CASCADE
                )
            """)

    template_db.close()

    # Compact the template so per-test copies are fast. The migrated
    # database is ~2.9MB mostly because of the 4KB default page size
    # (hundreds of tables/indexes each occupy at least one page); the
    # function-scoped ``db`` fixture copies this file for every test
    # (~4000+ copies per run). Rebuild it on 1KB pages via VACUUM, which
    # typically shrinks the file ~4x and keeps identical content.
    import sqlite3 as _sqlite3

    _compact_conn = _sqlite3.connect(template_path)
    try:
        _compact_conn.execute("PRAGMA page_size=1024")
        _compact_conn.execute("VACUUM")
    finally:
        _compact_conn.close()

    if old_db_conf:
        config.set("database", old_db_conf)

    yield template_path
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def db(setup_config, migrated_db_template):
    """
    Create a clean database for each test.

    Uses a fresh copy of the migrated template file for each test for
    complete isolation without re-running the migration chain.
    """
    import shutil
    from src.core.database import Database

    # Create a temporary database file for this test from the template
    temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    temp_db.close()
    shutil.copy2(migrated_db_template, temp_db.name)

    # Point the live config at this test's database without a YAML write.
    # ``config.set()`` would dump the whole file per test; swapping the
    # loader's dict directly is equivalent (cache is invalidated) and cheap.
    import copy
    from utils.config import _config_instance as _config_loader

    old_db_conf = copy.deepcopy(_config_loader.config.get("database"))
    # Include the small per-connection caches in the replaced config too:
    # when the singleton must be rebuilt (rare postgres-bound path below),
    # ``_init_once`` reads THIS dict — without ``connection_pool`` it would
    # fall back to the production 32MB cache + 256MB mmap defaults.
    _config_loader.config["database"] = {
        "type": "sqlite",
        "path": temp_db.name,
        "connection_pool": dict(_TEST_DB_POOL_CONFIG),
    }
    _config_loader._invalidate_cache()

    # ``Database`` is a process-wide singleton (``__new__`` returns the
    # cached ``_instance`` and ``__init__`` runs only once), so the first
    # construction — inside ``migrated_db_template`` — permanently binds the
    # engine to the *template* path. Every later ``Database()`` returned that
    # same instance, so all tests silently wrote into the shared template:
    # any test registering a hardcoded username (e.g. presence
    # ``test_edge_cases`` registering ``"testuser"``) then saw the previous
    # test's row and failed with UserExistsError.
    #
    # Instead of resetting the singleton per test (which would spin up a
    # fresh connection pool + pool-monitor thread per test and OOM the
    # ~6800-test suite), reuse the single instance and re-point it at this
    # test's fresh copy: update the engine config in place, drop the
    # previous test's thread-local connection, close the previous pool's
    # connections, and let ``connect()`` rebuild the pool against the new
    # path.
    db = Database()
    # A prior test (e.g. the PostgreSQL recovery fixtures) may have
    # re-bound the singleton to a postgres engine via ``Database.reset()``.
    # This fixture can only re-point a *sqlite* engine, so force a rebuild
    # whenever the singleton is bound to anything else. This is rare (only
    # postgres fixtures trigger it), so the per-test cost is negligible.
    if getattr(db, "type", "sqlite") != "sqlite":
        Database.reset()
        db = Database()
    if db.engine is not None:
        try:
            db.config["path"] = temp_db.name
            # ``connect()`` sizes the rebuilt pool from the singleton's OWN
            # config dict — not the loader dict swapped above. A prior test
            # in this worker that rebuilt the singleton with a smaller pool
            # (e.g. pool-sizing tests using max_connections=5) would
            # otherwise leak that size into every later test, starving
            # concurrent tests with "connection pool exhausted" failures.
            # Force the standard per-test pool config back on every test.
            db.config["connection_pool"] = dict(_TEST_DB_POOL_CONFIG)
            if isinstance(db.engine.config, dict):
                db.engine.config["path"] = temp_db.name
                db.engine.config["connection_pool"] = dict(_TEST_DB_POOL_CONFIG)
        except Exception:
            pass
    if getattr(db, "_local", None) is not None:
        old_conn = getattr(db._local, "connection", None)
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass
        db._local.connection = None
    pool = getattr(db, "_pool", None)
    if pool is not None:
        try:
            with pool._lock:
                # Close connections that were checked out during the test
                # but never returned (the source of ResourceWarning about
                # unclosed sqlite3.Connection objects). _in_use holds IDs,
                # _by_id maps IDs to the actual connection objects.
                by_id = getattr(pool, "_by_id", {})
                for conn_id in list(pool._in_use):
                    conn = by_id.get(conn_id)
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                for conn in list(pool._available):
                    try:
                        conn.close()
                    except Exception:
                        pass
                pool._available.clear()
                pool._in_use.clear()
                by_id.clear()
                getattr(pool, "_checked_out", {}).clear()
                pool._total_created = 0
                # Wake any worker threads blocked in ``get_connection`` on
                # THIS pool (e.g. an anyio ``run_in_threadpool`` query leaked
                # from the previous test). Without a notify they sleep the
                # full ``connect_timeout`` (30s default) while holding an
                # anyio thread-limiter slot — a later test's
                # ``run_in_threadpool`` call then queues behind them for 30s
                # (the "slow" websocket identify test was this exact case).
                # After the wake they re-check the (now reset) counters and
                # build a fresh connection against the re-pointed path.
                pool._condition.notify_all()
        except Exception:
            pass
        db._pool = None
    try:
        db.monitor._connection_metadata.clear()
    except Exception:
        pass
    # The singleton's query cache keys do not include the database path, so
    # cached SELECT results from the previous test's DB file would be served
    # to this test's fresh copy (e.g. a stale ``webhook_retries_dead_letter``
    # count). Drop it whenever we re-point the shared instance.
    try:
        with db._query_cache_lock:
            db._query_cache.clear()
    except Exception:
        pass
    db.connect()

    # REAL hashing/verification runs for every test: the session template
    # pins test-friendly Argon2id cost parameters (time_cost=1,
    # memory_cost=8KiB), so a real hash costs well under a millisecond and
    # the suite exercises the actual Argon2 implementation instead of a
    # fake. Tests that need the historical deterministic behaviour opt in
    # explicitly via the ``fake_crypto`` fixture below.
    yield db

    # Cleanup
    try:
        db.close()
    except Exception:
        pass

    # Close ANY other connections the singleton still tracks — e.g. ones
    # checked out into AnyIO worker-thread locals by ``run_in_threadpool``
    # queries that outlived the test (identify's READY payload used to do
    # exactly this via the global servers module). ``db.close()`` above
    # only releases the CURRENT thread's connection; without this pass the
    # worker-thread sqlite3 handles stay open against the (soon-to-be-
    # deleted) temp file for the rest of the process, accumulating fds and
    # deleted-inode references across the suite. Closing is safe:
    # ``_get_conn`` detects a closed connection and transparently
    # reconnects on the worker's next use.
    try:
        for _conn_id, _meta in list(db.monitor._connection_metadata.items()):
            _conn = _meta.get("connection")
            if _conn is not None and _conn is not getattr(
                db._local, "connection", None
            ):
                try:
                    _conn.close()
                except Exception:
                    pass
        db.monitor._connection_metadata.clear()
    except Exception:
        pass

    # Drop any cached workbook sessions left behind by the SumManager.
    # These hold live sqlite3.Connection objects in the module-level
    # _WORKBOOK_SESSIONS cache; without cleanup they trigger
    # ResourceWarning: unclosed database when the process exits.
    try:
        from src.core.suite.sum import manager as _sum_manager

        with _sum_manager._SESSION_GUARD:
            for _session in _sum_manager._WORKBOOK_SESSIONS.values():
                try:
                    _session.connection.close()
                except Exception:
                    pass
            _sum_manager._WORKBOOK_SESSIONS.clear()
        # Also clean up any orphaned temp files from sessions that were
        # evicted but whose temp files were not removed.
        _tmp_paths = getattr(_sum_manager, "_SESSION_TMP_PATHS", {})
        for _path in list(_tmp_paths.values()):
            try:
                os.unlink(_path)
            except OSError:
                pass
        _tmp_paths.clear()
        # Clear any per-workbook session locks to prevent stale references.
        _wb_locks = getattr(_sum_manager, "_WORKBOOK_SESSION_LOCKS", {})
        _wb_locks.clear()
    except Exception:
        pass

    # Remove the temp copy AND its WAL/SHM sidecars so /tmp (tmpfs —
    # RAM-backed) doesn't accumulate thousands of files across the run.
    for _path in (temp_db.name, f"{temp_db.name}-wal", f"{temp_db.name}-shm"):
        try:
            os.unlink(_path)
        except Exception:
            pass

    # Restore the database config
    if old_db_conf is not None:
        _config_loader.config["database"] = old_db_conf
        _config_loader._invalidate_cache()


@pytest.fixture
def test_db(db):
    """Alias for db fixture for backward compatibility."""
    return db


@pytest.fixture
def fake_crypto():
    """Explicit opt-in: deterministic fake hashing/verification.

    Request this fixture ONLY in tests that need the historical
    ``fake_hash_$test`` semantics (e.g. asserting hash round-trips without
    real Argon2). Every other test runs the real (test-cost) Argon2
    implementation.
    """
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        with patch.object(encryption, "verify_password", return_value=True):
            yield


@pytest.fixture
def lenient_email_validation():
    """Explicit opt-in: reserved-TLD email whitelist accommodation.

    Patches ``auth.passwords.validate_email`` AND the registration
    manager's imported reference so addresses on RFC 6761 reserved TLDs
    (``.test``/``.local``/``.example``/``.invalid``) pass after the real
    validator rejects them. The production validator is untouched for
    every test that does not request this fixture.
    """
    from src.core.auth import passwords as _auth_passwords
    from src.core.auth.managers import registration as _reg_module

    import src.tests._conftest.config_env as _config_env

    with patch.object(
        _auth_passwords, "validate_email", _config_env._test_always_valid_email
    ):
        with patch.object(
            _reg_module, "validate_email", _config_env._test_always_valid_email
        ):
            yield
