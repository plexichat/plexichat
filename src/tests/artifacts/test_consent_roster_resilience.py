"""Resilience of voice-call consent/participant paths to roster failures.

Covers two related defects:

1. A transient DB failure (pool exhaustion under burst load, lock
   contention) during the ``voice_states`` membership lookup used to be
   treated as an authoritative EMPTY roster: live members were rejected
   with a bogus "not currently in voice channel" error, and — worse —
   recording could start because an empty set is trivially a subset of
   every consent set (fail OPEN). Lookups are now retried, and recording
   decisions fail CLOSED when the roster stays unreadable.

2. The per-test ``db`` fixture reused the ``Database`` singleton's own
   config dict without restoring the pool sizing, so a test that rebuilt
   the singleton with a small pool (max_connections=5) leaked that size
   into every later test in the worker — starving the 8-thread race
   tests with "connection pool exhausted". The last two tests in this
   file pin that isolation (they run in definition order).
"""

from unittest.mock import Mock

from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.voice_calls import VoiceCallManager

CHANNEL_ID = 77
SERVER_ID = 22


def _seed_voice_state(db, user_id, channel_id, server_id=SERVER_ID):
    db.execute(
        "INSERT INTO voice_states "
        "(user_id, channel_id, server_id, joined_at, last_activity) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, channel_id, server_id, 1, 1),
    )


def _voice_manager(db, recording_manager=None):
    manager = VoiceCallManager(
        db,
        artifact_manager=ArtifactManager(db, {"enabled": True}),
        config={"voice": {"allow_recording": True}},
    )
    manager.set_recording_manager(
        recording_manager if recording_manager is not None else Mock()
    )
    return manager


def _pool_exhausted():
    return TimeoutError("SQLite connection pool exhausted: no available connections")


def test_transient_roster_failures_are_retried(db, monkeypatch):
    """Burst-load pool exhaustion must not reject live members."""
    import src.core.artifacts.voice_calls as voice_calls_mod

    real_lookup = voice_calls_mod.get_channel_voice_user_ids
    calls = {"n": 0}

    def flaky(db_handle, channel_id):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise _pool_exhausted()
        return real_lookup(db_handle, channel_id)

    monkeypatch.setattr(voice_calls_mod, "get_channel_voice_user_ids", flaky)
    # No sleeping in tests: retries still happen, just without delay.
    monkeypatch.setattr(VoiceCallManager, "_ROSTER_LOOKUP_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(voice_calls_mod.random, "uniform", lambda *a, **k: 0.0)

    manager = _voice_manager(db)
    _seed_voice_state(db, 501, CHANNEL_ID)
    call = manager.start_call(
        channel_id=CHANNEL_ID, server_id=SERVER_ID, initiator_id=501
    )
    manager.add_consent(call.id, 501)

    refreshed = manager.get_active_by_channel(CHANNEL_ID)
    assert refreshed is not None
    assert {int(v) for v in refreshed.consented_participants} == {501}
    assert calls["n"] > 3  # the flaky attempts really were retried


def test_consent_does_not_start_recording_when_roster_unreadable(db, monkeypatch):
    """Fail closed: consent persists, but no recording starts on doubt."""
    import src.core.artifacts.voice_calls as voice_calls_mod

    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 502, CHANNEL_ID)
    call = manager.start_call(
        channel_id=CHANNEL_ID, server_id=SERVER_ID, initiator_id=502
    )
    recording.reset_mock()

    # Break only the FINAL completion lookup: let the membership gate see a
    # healthy roster, then fail everything after.
    seen = {"gate_passed": False}

    def gate_once(db_handle, channel_id):
        if not seen["gate_passed"]:
            seen["gate_passed"] = True
            return [502]
        raise _pool_exhausted()

    monkeypatch.setattr(voice_calls_mod, "get_channel_voice_user_ids", gate_once)
    # No sleeping in tests: retries still happen, just without delay.
    monkeypatch.setattr(VoiceCallManager, "_ROSTER_LOOKUP_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(voice_calls_mod.random, "uniform", lambda *a, **k: 0.0)

    manager.add_consent(call.id, 502)

    refreshed = manager.get_active_by_channel(CHANNEL_ID)
    assert refreshed is not None
    # Consent itself is durable even when the roster is unreadable ...
    assert {int(v) for v in refreshed.consented_participants} == {502}
    # ... but recording must NOT start on an unproven unanimity.
    recording.start_call_recording.assert_not_called()


def test_new_join_stops_recording_when_roster_unreadable(db, monkeypatch):
    """Fail closed the other way: an unverifiable newcomer stops the segment."""
    import src.core.artifacts.voice_calls as voice_calls_mod

    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 503, CHANNEL_ID)
    call = manager.start_call(
        channel_id=CHANNEL_ID, server_id=SERVER_ID, initiator_id=503
    )
    manager.add_consent(call.id, 503)
    recording.reset_mock()

    _seed_voice_state(db, 504, CHANNEL_ID)
    gate_calls = {"n": 0}

    def gate_once(db_handle, channel_id):
        gate_calls["n"] += 1
        if gate_calls["n"] == 1:
            return [503, 504]  # in-lock membership check succeeds
        raise _pool_exhausted()  # completion re-check is unreadable

    monkeypatch.setattr(voice_calls_mod, "get_channel_voice_user_ids", gate_once)
    # No sleeping in tests: retries still happen, just without delay.
    monkeypatch.setattr(VoiceCallManager, "_ROSTER_LOOKUP_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(voice_calls_mod.random, "uniform", lambda *a, **k: 0.0)

    manager.add_participant(call.id, 504)
    recording.stop_call_recording.assert_called_once_with(call.id)


def test_duplicate_revoke_stays_a_noop_when_roster_unreadable(db, monkeypatch):
    """A no-op revoke must not stop an unrelated segment, even on DB doubt."""
    import src.core.artifacts.voice_calls as voice_calls_mod

    recording = Mock()
    manager = _voice_manager(db, recording)
    _seed_voice_state(db, 505, CHANNEL_ID)
    call = manager.start_call(
        channel_id=CHANNEL_ID, server_id=SERVER_ID, initiator_id=505
    )
    manager.add_consent(call.id, 505)
    manager.remove_consent(call.id, 505)
    recording.reset_mock()

    gate_calls = {"n": 0}

    def gate_once(db_handle, channel_id):
        gate_calls["n"] += 1
        if gate_calls["n"] == 1:
            return [505]  # membership gate succeeds; nothing withdrew
        raise _pool_exhausted()

    monkeypatch.setattr(voice_calls_mod, "get_channel_voice_user_ids", gate_once)
    # No sleeping in tests: retries still happen, just without delay.
    monkeypatch.setattr(VoiceCallManager, "_ROSTER_LOOKUP_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(voice_calls_mod.random, "uniform", lambda *a, **k: 0.0)

    manager.remove_consent(call.id, 505)  # duplicate revoke: already withdrawn
    recording.stop_call_recording.assert_not_called()


# --- db-fixture pool isolation (order-dependent pair, run in order) ---


def test_pool_shrinker_runs_first(db):
    """Shrink the singleton pool to max 1, mimicking pool-sizing tests.

    The ``db`` fixture teardown intentionally does NOT restore the
    singleton's own pool sizing — the NEXT test proves the fixture setup
    restores it (regression: a leaked max_connections=5 starved the
    threaded race tests with "pool exhausted").
    """
    from src.core.database import Database
    import utils.config as config

    Database.reset()
    shrunk = dict(config.get("database"))
    shrunk_pool = dict(shrunk.get("connection_pool", {}))
    shrunk_pool["max_connections"] = 1
    shrunk["connection_pool"] = shrunk_pool
    config.set("database", shrunk)
    rebuilt = Database()
    rebuilt.connect()

    assert rebuilt.get_pool_stats()["max_connections"] == 1


def test_pool_restored_to_standard_size(db):
    """The ``db`` fixture must restore the standard pool after a shrink."""
    assert db.get_pool_stats()["max_connections"] == 16
