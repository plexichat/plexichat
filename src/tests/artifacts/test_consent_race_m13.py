"""Regression tests for M13: consent / participant lost-update race.

Two concurrent consent joins used to read-modify-write the participant
roster without a lock (and, historically, the persisted consent list without
a transaction), so one update silently dropped the other and recording could
start on an incomplete roster.

These tests hammer ``add_consent`` / ``add_participant`` from multiple
threads on the same ``call_id`` and assert no update is ever lost. On the
pre-fix code the consent test drops consents (stale read-modify-write) and
the join test leaves the in-memory snapshot missing members.
"""

import threading
from unittest.mock import Mock

from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.voice_calls import VoiceCallManager

CHANNEL_ID = 91
SERVER_ID = 22
N_PARTICIPANTS = 8


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


def _run_threads(targets):
    """Run zero-arg callables concurrently; re-raise the first failure."""
    errors = []
    threads = []
    start = threading.Barrier(len(targets))

    def _wrap(fn):
        try:
            start.wait(timeout=10)
            fn()
        except Exception as exc:  # collected and re-raised
            errors.append(exc)

    for fn in targets:
        t = threading.Thread(target=_wrap, args=(fn,))
        threads.append(t)
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "worker thread hung"
    assert not errors, f"concurrent calls raised: {errors!r}"


def test_concurrent_consents_all_persist(db):
    """Parallel consents on one call must all land in the consent list."""
    manager = _voice_manager(db)
    user_ids = [1000 + i for i in range(N_PARTICIPANTS)]
    for uid in user_ids:
        _seed_voice_state(db, uid, CHANNEL_ID)
    call = manager.start_call(
        channel_id=CHANNEL_ID, server_id=SERVER_ID, initiator_id=user_ids[0]
    )

    _run_threads(
        [lambda uid=uid: manager.add_consent(call.id, uid) for uid in user_ids]
    )

    refreshed = manager.get_active_by_channel(CHANNEL_ID)
    assert refreshed is not None
    consented = {int(v) for v in refreshed.consented_participants}
    assert set(user_ids).issubset(consented), (
        f"lost consent updates: missing {set(user_ids) - consented}"
    )


def test_concurrent_joins_snapshot_covers_all_participants(db):
    """Parallel joins must never drop a member from the live snapshot."""
    manager = _voice_manager(db)
    # The snapshot mutation path is guarded by this lock (M13 fix).
    assert hasattr(manager, "_participants_lock"), (
        "VoiceCallManager must guard participant mutation with _participants_lock"
    )
    initiator = 2000
    joiners = [2001 + i for i in range(N_PARTICIPANTS)]
    _seed_voice_state(db, initiator, CHANNEL_ID)
    call = manager.start_call(
        channel_id=CHANNEL_ID, server_id=SERVER_ID, initiator_id=initiator
    )
    for uid in joiners:
        _seed_voice_state(db, uid, CHANNEL_ID)

    _run_threads(
        [lambda uid=uid: manager.add_participant(call.id, uid) for uid in joiners]
    )

    live = manager._current_participant_ids(CHANNEL_ID)
    assert live == {initiator, *joiners}
    with manager._participants_lock:
        snapshot = set(manager._voice_call_participants[int(call.id)])
    assert snapshot == live, (
        f"lost participant updates: snapshot={sorted(snapshot)} live={sorted(live)}"
    )
    refreshed = manager.get_active_by_channel(CHANNEL_ID)
    assert refreshed is not None
    assert int(refreshed.participant_count) >= len(live)
