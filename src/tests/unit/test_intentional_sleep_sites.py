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
    "api/test_ack_server_channel_block_audience.py": (
        1,
        "drain task queue so the fire-and-forget audience fan-out runs",
    ),
    "api/test_admin_read_cache_single_flight.py": (
        2,
        "poll until every cold caller is inside the loader, then leave the leader a window to fill the cache before releasing the gate",
    ),
    "api/test_admin_summary_cache_singleflight.py": (
        1,
        "poll a predicate until the coalesced build finishes",
    ),
    "api/test_maintenance_sweep_interval_hardening.py": (
        1,
        "let the background sweep run one interval so the NaN-interval crash would surface",
    ),
    "api/test_message_audience_fail_closed.py": (
        1,
        "drain task queue so the audience fan-out completes before assertions",
    ),
    "api/test_pin_fanout_strips_viewer_fields.py": (
        1,
        "drain task queue so the fire-and-forget pin broadcast runs",
    ),
    "api/test_typing_channel_block_audience.py": (
        1,
        "drain task queue so the typing audience fan-out runs",
    ),
    "api/test_typing_conversation_id_alias_gate.py": (
        1,
        "drain task queue so the typing fan-out runs before assertions",
    ),
    "artifacts/test_capability_cache_inflight_coalescing.py": (
        1,
        "hold the leader inside the loader so the followers are observably waiting",
    ),
    "auth/test_2fa_attempt_atomic_incr_expire.py": (
        1,
        "stand in for the cache round trip that makes the increment race deterministic",
    ),
    "auth/test_api_token_required_cache_singleflight.py": (
        1,
        "serialized cold-loader delay so N callers cost N delays without single-flight",
    ),
    "auth/test_device_revocation_socket_closes.py": (
        1,
        "yield to the loop so the scheduled socket close runs before the loop is torn down",
    ),
    "auth/test_disable_2fa_no_exception_closes_all_sockets.py": (
        1,
        "yield to the loop so every scheduled revocation close runs",
    ),
    "auth/test_disable_2fa_revocation_socket_close.py": (
        1,
        "yield to the loop so the scheduled socket close runs",
    ),
    "auth/test_refresh_replay_revoke_closes_session_socket.py": (
        1,
        "yield to the loop so the replay revocation closes the session socket",
    ),
    "auth/test_remaining_revocation_socket_closes.py": (
        2,
        "yield to the loop so a scheduled revocation close runs before the loop ends",
    ),
    "auth/test_revocation_closes_gateway_sockets.py": (
        1,
        "yield to the loop so the gateway socket close is dispatched",
    ),
    "auth/test_revocation_closes_gateway_sockets_routes.py": (
        3,
        "yield to the loop so the route-triggered revocation closes its sockets",
    ),
    "auth/test_session_id_rotation_on_2fa_transition.py": (
        1,
        "yield to the loop so the rotation's socket close is dispatched",
    ),
    "auth/test_single_session_revoke_closes_matching_socket.py": (
        1,
        "yield to the loop so the single-session revoke closes its socket",
    ),
    "auth/test_token_verify_cache_singleflight.py": (
        1,
        "serialized verify delay so N callers cost N delays without single-flight",
    ),
    "auth/test_username_blacklist_plan_cache.py": (
        1,
        "let the bounded blacklist plan TTL expire so the remote change is observed",
    ),
    "embeds/test_link_preview_inflight_coalescing.py": (
        3,
        "hold the leader fetch open long enough for the followers to join it",
    ),
    "features/test_report_evidence_url_fanout_perf.py": (
        1,
        "synthetic per-URL latency in the stubbed check so fan-out overlap is measurable",
    ),
    "messaging/test_message_fanout_strips_viewer_fields.py": (
        1,
        "drain task queue so the message fan-out completes before assertions",
    ),
    "security/test_credential_strength_final_guard.py": (
        1,
        "retry the source read while another worker rewrites the file atomically",
    ),
    "security/test_invite_code_entropy.py": (
        1,
        "retry the generator source read while the file is being rewritten",
    ),
    "security/test_shared_config_cache_copy_isolation_guard.py": (
        1,
        "retry the source read while another worker rewrites the file atomically",
    ),
    "servers/test_has_perm_verdict_cache_singleflight.py": (
        1,
        "per-call permission loader delay so the coalesced path is measurable",
    ),
    "unit/test_admin_grant_cache_unlocked_hit.py": (
        2,
        "model the cost of an uncached lookup, and let a short grant TTL expire",
    ),
    "unit/test_content_filter_pattern_cache_perf.py": (
        1,
        "slow compile double so a cached pattern is observably faster",
    ),
    "webhooks/test_delivery_bounded_concurrency.py": (
        2,
        "stand-in delivery latency so concurrent deliveries overlap observably",
    ),
    "webhooks/test_signature_lock_keying_invariants.py": (
        1,
        "give the contending thread time to block on the signature lock",
    ),
    "api/test_admin_dashboard_summary_warmup.py": (
        1,
        "slow-build double widens the window where two readers race the cache fill",
    ),
    "api/test_admin_license_feature_catalog_cache.py": (
        1,
        "stubbed provider delay stands in for a cold catalog build so the fill is observable",
    ),
    "api/test_read_receipt_dm_block_audience.py": (
        1,
        "yield to the loop so the fire-and-forget ACK broadcast runs",
    ),
    "auth/test_api_token_verify_cache.py": (
        1,
        "let the short access-token verify cache TTL expire",
    ),
    "automod/test_ai_check_backpressure.py": (
        1,
        "poll a backpressure predicate until it flips inside a stubbed caller",
    ),
    "automod/test_compiled_rule_cache.py": (
        1,
        "slow rule double widens the window where two readers race the compile cache",
    ),
    "api/test_admin_migrations_list_cache.py": (
        1,
        "slow-build double widens the window where two readers race the cache fill",
    ),
    "api/test_capability_catalog_warmup.py": (
        1,
        "stubbed catalog stands in for the cold provider probe so the warm-up overlap is measurable",
    ),
    "api/test_policy_cache_startup_warmup.py": (
        1,
        "slow policy-build double stands in for the cold config/prefix build so the warm-up hit is measurable",
    ),
    "api/test_dual_tombstone_contract.py": (
        1,
        "yield to the loop so fire-and-forget create_task() broadcasts run",
    ),
    "api/test_me_hotpath_hardening.py": (
        1,
        "slow cache double widens the window where the hot-path read races the fill",
    ),
    "api/test_pin_broadcast_parity.py": (
        1,
        "yield to the loop so fire-and-forget create_task() broadcasts run",
    ),
    "api/test_typing_block_audience.py": (
        1,
        "yield to the loop so the fire-and-forget typing broadcast runs",
    ),
    "api/routes/test_avatars_hardening.py": (
        1,
        "let the bounded avatar checksum-cache TTL expire",
    ),
    "artifacts/test_transcription_scheduling_hardening.py": (
        1,
        "poll for the scheduled transcription worker to pick up the queued row",
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
    "database/test_cache_request_local_coalescing.py": (
        1,
        "yield to the loop so a concurrent request coalesces onto the in-flight fill",
    ),
    "database/test_cache_single_flight_swr.py": (
        7,
        "yield to the loop while concurrent loaders coalesce; poll until the leader registers",
    ),
    "media/test_video_metadata_dedup.py": (
        2,
        "poll the coalescing counter, then let the metadata dedup window elapse",
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
    "events/test_cross_worker_dedupe.py": (
        1,
        "wait out a 10ms dedupe TTL before asserting the id is reprocessed",
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
    "api/test_messages_route_security_hardening.py": (
        1,
        "yield to the loop so the fire-and-forget broadcast task runs",
    ),
    "database/test_pool_fairness.py": (
        2,
        "hold a pool worker briefly so contention between checkouts is measurable",
    ),
    "media/test_result_cache.py": (
        1,
        "let the bounded result-cache TTL advance",
    ),
    "security/test_deep_abuse_regressions.py": (
        2,
        "yield inside the abuse stub between rate-limited attempts",
    ),
    "unit/test_config_cache_perf_regression.py": (
        1,
        "simulate a slow config loader so the cache window is measurable",
    ),
    "notifications/test_background_dispatcher_hardening.py": (
        1,
        "poll interval inside the local _wait_for helper",
    ),
    "plexijoin/test_admin_connections_hardening.py": (
        1,
        "slow decrypt double widens the window where the connection list races the fill",
    ),
    "suite/test_home_perf_hardening.py": (
        1,
        "slow feed lister widens the window where the home feed races the cache fill",
    ),
    "unit/test_channel_event_audience_hardening.py": (
        1,
        "drain helper yields to the loop so queued audience tasks run",
    ),
    "unit/test_gateway_outbound_batching.py": (
        1,
        "pace the batch window so the flush boundary is crossed",
    ),
    "unit/test_gateway_throughput_regression.py": (
        1,
        "advance the fixed throughput measurement window",
    ),
    "unit/test_gateway_batching_optimization.py": (
        4,
        "yield to the event loop so the batcher's gather window and flush run",
    ),
    "unit/test_valkey_lock_backoff_scaling.py": (
        3,
        "yield across loop turns so holders overlap, then sleep out a short backoff",
    ),
    "unit/test_inprocess_cache_bounds.py": (
        2,
        "spin until the holder thread enters the lock; hold it so the waiter is provably blocked",
    ),
    "unit/test_message_render_cache.py": (
        1,
        "let the bounded render-cache TTL advance",
    ),
    "unit/test_push_async_fanout.py": (
        3,
        "yield to the loop so queued fan-out tasks run, and pace the async batch",
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
