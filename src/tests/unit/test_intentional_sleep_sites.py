"""Every ``.sleep()`` left in the test tree is an audited, intentional wait.

Earlier determinism sweeps replaced fixed sleeps that *guessed* how long a
background effect takes with the bounded condition waits in
``src/tests/_conftest/waiting.py``. The sleeps that remain are deliberate:
they either space two operations so a timestamp advances, let a thread start
before a concurrent action, pace a synthetic media track, or poll a bounded
external condition -- and each one is recorded below with the reason it
exists, so the next sweep does not have to re-triage the whole tree.

This registry is a gate, not just documentation: a new ``time.sleep`` /
``asyncio.sleep`` (including an extra one in a listed file) fails until it is
either added here with a reason or replaced with ``wait_until`` /
``wait_until_async``. ``_conftest/`` is excluded because the wait helper
legitimately sleeps between polls.
"""

from __future__ import annotations

import re
from pathlib import Path

# src/tests/unit/test_intentional_sleep_sites.py -> src/tests
TESTS_DIR = Path(__file__).resolve().parents[1]

# A call, not a mention: skip comment-only lines (a bare ``# time.sleep(...)``
# note) but keep the inline-comment form (``time.sleep(0.1)  # why``).
SLEEP_CALL = re.compile(r"^\s*[^#\n]*\.sleep\(", re.MULTILINE)

# relative path under src/tests -> (number of .sleep() calls, why they exist)
INTENTIONAL_SLEEPS: dict[str, tuple[int, str]] = {
    "api/test_dual_tombstone_contract.py": (
        1,
        "yield to the loop so fire-and-forget create_task() broadcasts run",
    ),
    "api/test_pin_broadcast_parity.py": (
        1,
        "yield to the loop so fire-and-forget create_task() broadcasts run",
    ),
    "artifacts/test_voice_calls.py": (
        1,
        "poll a release event inside a stubbed recorder coroutine",
    ),
    "auth/test_audit.py": (
        1,
        "space two logins so their audit rows get distinct timestamps",
    ),
    "auth/test_operations.py": (
        1,
        "let a zero-TTL user-cache entry expire",
    ),
    "auth/test_sessions.py": (
        1,
        "space sequential logins so session timestamps differ",
    ),
    "auth/test_totp_replay_shared.py": (
        1,
        "wait out the one-second TOTP replay window",
    ),
    "avatars/test_checksum_cache_bound_m5.py": (
        1,
        "let the bounded checksum-cache TTL expire",
    ),
    "database/test_security.py": (
        3,
        "spin on a concurrent writer's event; hold a transaction open; "
        "space worker writes",
    ),
    "encryption/test_encryption.py": (
        2,
        "space snowflake ids so timestamps order; wait out int()-second key rotation",
    ),
    "encryption/test_key_rotation.py": (
        1,
        "let the rotation thread start before forcing a concurrent rotate",
    ),
    "events/test_queued_delivery_integration.py": (
        1,
        "let the consumer's 0.05s poll interval elapse",
    ),
    "fixtures/postgres_docker.py": (
        1,
        "poll for Postgres readiness between container start attempts",
    ),
    "fixtures/valkey_docker.py": (
        1,
        "poll for Valkey readiness before returning",
    ),
    "media/test_thumbnails_signing.py": (
        1,
        "space signing from verification so created_at precedes the check",
    ),
    "performance/test_stress.py": (
        2,
        "fixed soak windows in deselected performance tests",
    ),
    "presence/test_typing.py": (
        1,
        "space two typing-indicator updates so timestamps differ",
    ),
    "ratelimit/test_async_path.py": (
        7,
        "yield to the loop (sleep(0)) in async storage doubles",
    ),
    "ratelimit/test_storage.py": (
        1,
        "hold the lock so a concurrent acquire blocks, asserting max concurrency",
    ),
    "security/test_session_hijacking.py": (
        1,
        "space token verification from the hijack attempt",
    ),
    "settings/test_settings.py": (
        1,
        "space two settings writes so timestamps differ",
    ),
    "suite/test_stash_sum.py": (
        1,
        "space two creates so millisecond timestamps order",
    ),
    "test_database_load.py": (
        2,
        "sampler interval and inter-batch pause in a deselected load test",
    ),
    "test_database_pool.py": (
        3,
        "let connections exceed max age/idle before lazy eviction",
    ),
    "test_pool_monitoring.py": (
        2,
        "let the monitor thread tick; poll inside the stats loop",
    ),
    "test_production_simulation.py": (
        8,
        "let spawned workers start; fixed observation windows in the "
        "deselected simulation",
    ),
    "test_sqlite_load.py": (
        1,
        "let concurrent writer threads start",
    ),
    "test_sqlite_pool_reclaim.py": (
        13,
        "let leaked connections idle past the reclaim threshold; spin "
        "until worker threads exit",
    ),
    "test_valkey_docker.py": (
        2,
        "wait out a lock TTL; poll pub/sub for a message",
    ),
    "voice/test_security.py": (
        1,
        "space TURN credential issuance so they differ",
    ),
    "voice/test_sfu_aiortc_degraded_media.py": (
        1,
        "pace the synthetic video track at 30fps",
    ),
    "voice/test_sfu_aiortc_fanout_media.py": (
        1,
        "pace the synthetic video track at 30fps",
    ),
    "voice/test_sfu_aiortc_layer_hint_apply.py": (
        2,
        "park a never-pulled track forever (3600s, marked no-cover)",
    ),
    "voice/test_sfu_aiortc_media.py": (
        1,
        "pace the synthetic audio track at 20ms",
    ),
    "voice/test_sfu_fanout.py": (
        2,
        "yield to the loop so a consumer drains each produced frame",
    ),
    "voice/test_voice_media_handshake_e2e.py": (
        1,
        "pace the synthetic audio track at 20ms",
    ),
    "voice/test_w5a_fixes.py": (
        1,
        "yield to the loop so the spawned disconnect task starts",
    ),
    "webhooks/test_management.py": (
        1,
        "space an update so updated_at exceeds created_at",
    ),
    "webhooks/test_token.py": (
        1,
        "space a token regen so updated_at exceeds created_at",
    ),
    "websocket/test_connection.py": (
        3,
        "space heartbeat/ack updates so latency is positive",
    ),
    "websocket/test_cross_worker_chaos.py": (
        1,
        "yield to the loop so the chaos task ticks",
    ),
    "websocket/test_edge_cases.py": (
        1,
        "space an activity update so its timestamp advances",
    ),
    "websocket/test_heartbeat.py": (
        5,
        "space heartbeat/ack updates so latencies are positive",
    ),
    "websocket/test_security.py": (
        1,
        "space a heartbeat so the timestamp changes",
    ),
}


def _sleep_counts() -> dict[str, int]:
    """Map every test-tree path with a ``.sleep()`` call to its call count."""
    counts: dict[str, int] = {}
    this_file = Path(__file__).resolve()
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if "_conftest" in path.parts or path.resolve() == this_file:
            continue
        found = len(SLEEP_CALL.findall(path.read_text(encoding="utf-8")))
        if found:
            counts[path.relative_to(TESTS_DIR).as_posix()] = found
    return counts


def test_every_sleep_site_is_registered():
    actual = _sleep_counts()
    unregistered = {p: n for p, n in actual.items() if p not in INTENTIONAL_SLEEPS}
    assert not unregistered, (
        "new .sleep() site(s) with no registered reason: "
        f"{unregistered}. Replace them with wait_until/wait_until_async, or "
        "add each to INTENTIONAL_SLEEPS with why it is deliberate "
        "(see src/tests/README.md)."
    )


def test_registry_has_no_stale_entries():
    actual = _sleep_counts()
    stale = sorted(set(INTENTIONAL_SLEEPS) - set(actual))
    assert not stale, (
        "INTENTIONAL_SLEEPS lists files with no .sleep() left: "
        f"{stale}. The site moved or was made deterministic; update the registry."
    )


def test_registered_sleep_counts_are_exact():
    actual = _sleep_counts()
    drifted = {
        path: (registered, actual[path])
        for path, (registered, _reason) in INTENTIONAL_SLEEPS.items()
        if path in actual and actual[path] != registered
    }
    assert not drifted, (
        "sleep count changed (registered, actual): "
        f"{drifted}. Keep the audit accurate by updating INTENTIONAL_SLEEPS."
    )


def test_every_registered_site_records_a_reason():
    blank = [
        path
        for path, (_count, reason) in INTENTIONAL_SLEEPS.items()
        if not reason.strip()
    ]
    assert not blank, f"registry entries missing a reason: {blank}"
