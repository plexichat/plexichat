"""Token verification resolves trusted-device state in the session row read.

``_verify_session_token`` used to issue a second SELECT against
``auth_devices`` (via ``_device_is_trusted_by_id``) whenever a session was
extended on activity.  The trust flag is a column of a table that can be
reached from the session row's own ``device_id``, so it is folded into the
session/user SELECT as a scalar subquery.  The extend path now costs one round
trip instead of two, and the trusted/untrusted TTL decision is unchanged.

The test drives the verifier directly (bypassing the short-lived verify cache)
and counts every ``fetch_one`` on the live database.
"""

from unittest.mock import patch

import pytest

from src.core.auth._lazy import _get_auth_manager
from src.core.auth.tokens import parse_token


def _mgr(auth_manager):
    return _get_auth_manager().get_instance()


def _set_session_config(auth_manager, **overrides):
    mgr = _mgr(auth_manager)
    cfg = dict(mgr._config.get("sessions", {}))
    cfg.update(overrides)
    mgr._config["sessions"] = cfg


def _register_login_with_device(auth_manager, username, fingerprint):
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(
            username, "TestPass123!", device_info={"fingerprint": fingerprint}
        )
    return user, result


@pytest.mark.unit
def test_trusted_device_state_loaded_in_one_session_read(db, auth_manager):
    user, result = _register_login_with_device(
        auth_manager, "verify_trust_coalesce", "verify_trust_coalesce_fp"
    )
    mgr = _mgr(auth_manager)
    _set_session_config(
        auth_manager,
        expire_hours=24,
        trusted_expire_hours=720,
        extend_on_activity=True,
        extend_threshold_hours=24,
    )
    assert auth_manager.trust_device(user.id, "verify_trust_coalesce_fp") is True

    parsed = parse_token(result.token)
    now = mgr._get_timestamp()
    # Force the extend path: idle last_activity and a near-expiry session.
    mgr._db.execute(
        "UPDATE auth_sessions SET expires_at = ?, last_activity = ? WHERE id = ?",
        (now + (12 * 3600 * 1000), now - (120 * 1000), parsed["id"]),
    )

    # Warm the memoized schema verdict so DDL probes are not counted.
    mgr._ensure_trusted_device_columns()

    original_fetch_one = mgr._db.fetch_one
    with patch.object(mgr._db, "fetch_one", wraps=original_fetch_one) as spy:
        info = mgr._verify_session_token(parsed, None)
    reads = [call.args[0] for call in spy.call_args_list]

    assert info.valid is True
    session_reads = [
        sql for sql in reads if "auth_sessions" in sql and "auth_users" in sql
    ]
    # A separate trust probe is the standalone ``SELECT trusted FROM
    # auth_devices ...`` query; the coalesced subquery lives inside the single
    # session read above and must not be counted as a second round trip.
    device_reads = [
        sql
        for sql in reads
        if sql.strip().startswith("SELECT trusted FROM auth_devices")
    ]
    assert len(session_reads) == 1, reads
    assert not device_reads, (
        "trusted-device state must be folded into the session read; "
        f"got separate reads: {device_reads}"
    )

    # The trusted TTL is still honored (coalescing must not change behavior).
    row = original_fetch_one(
        "SELECT expires_at FROM auth_sessions WHERE id = ?", (parsed["id"],)
    )
    expected = mgr._get_timestamp() + (720 * 3600 * 1000)
    assert abs(row["expires_at"] - expected) < 5000
