"""Regression: account erasure must purge residual credentials/tokens.

``auth_api_access_tokens.created_by`` and ``auth_passkey_challenges.user_id``
have no foreign key to ``auth_users``. The reaper only reaps *expired* passkey
challenges and never deletes API tokens, so ``purge_user`` used to drop the
account row while leaving the user's still-usable API token (encrypted token
material, last-used IP/UA/path) and pending passkey challenges behind under a
dead id. ``erase_user_residue`` now removes them; this test pins that behaviour
and that another subject's credentials are untouched.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.core.erasure import erase_user_residue, purge_user_credentials, sweep_orphans
from src.utils import encryption

pytestmark = pytest.mark.integration

NOW = 1_700_000_000_000


def _register(auth_manager, pii_gen, label):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"{label}_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _ensure_credential_child_tables(db):
    """Create the optional credential child tables when the fixture lacks them."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS auth_api_access_token_scopes ("
        "id INTEGER PRIMARY KEY, token_id INTEGER NOT NULL, "
        "scope_type TEXT NOT NULL, value TEXT NOT NULL, created_by INTEGER, "
        "created_at INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS auth_api_access_token_events ("
        "id INTEGER PRIMARY KEY, token_id INTEGER NOT NULL, "
        "used_at INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS auth_passkey_challenges ("
        "id INTEGER PRIMARY KEY, challenge_id TEXT UNIQUE NOT NULL, "
        "user_id INTEGER, challenge_type TEXT NOT NULL, challenge BLOB NOT NULL, "
        "expires_at INTEGER NOT NULL, used INTEGER DEFAULT 0, "
        "created_at INTEGER DEFAULT 0)"
    )


def _api_token(db, token_id, creator):
    db.execute(
        "INSERT INTO auth_api_access_tokens "
        "(id, token_index, token_encrypted, created_by, created_at, "
        "last_used_ip_encrypted, ua_encrypted, last_used_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            token_id,
            f"idx-{token_id}",
            f"ENCRYPTED-TOKEN-{token_id}",
            creator,
            NOW,
            "ENCRYPTED-IP",
            "ENCRYPTED-UA",
            "/private/path",
        ),
    )


def test_erase_purges_api_tokens_and_passkey_challenges(
    db, auth_manager, pii_gen, monkeypatch
):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    _ensure_credential_child_tables(db)
    alice = _register(auth_manager, pii_gen, "ercredsa")
    bob = _register(auth_manager, pii_gen, "ercredsb")
    aid, bid = alice.id, bob.id

    # Two tokens for the erasing subject (with sensitive children) plus one
    # belonging to a different subject that must survive untouched.
    alice_t1, alice_t2, bob_t = 8_100_000_001, 8_100_000_002, 8_100_000_003
    _api_token(db, alice_t1, aid)
    _api_token(db, alice_t2, aid)
    _api_token(db, bob_t, bid)
    db.execute(
        "INSERT INTO auth_api_access_token_scopes "
        "(id, token_id, scope_type, value, created_at) VALUES (?, ?, 'route', ?, ?)",
        (1, alice_t1, "/admin", NOW),
    )
    db.execute(
        "INSERT INTO auth_api_access_token_events "
        "(id, token_id, used_at) VALUES (?, ?, ?)",
        (1, alice_t1, NOW),
    )
    challenge_cols = (
        "INSERT INTO auth_passkey_challenges "
        "(id, challenge_id, user_id, challenge_type, challenge, expires_at, "
        "created_at) VALUES (?, ?, ?, 'register', ?, ?, ?)"
    )
    db.execute(
        challenge_cols, (1, "challenge-alice", aid, b"secret", NOW + 60_000, NOW)
    )
    db.execute(challenge_cols, (2, "challenge-bob", bid, b"secret", NOW + 60_000, NOW))

    result = erase_user_residue(db, aid)

    # Every credential owned by the erased subject is gone.
    assert (
        db.fetch_one(
            "SELECT 1 FROM auth_api_access_tokens WHERE created_by = ?", (aid,)
        )
        is None
    )
    assert (
        db.fetch_one(
            "SELECT 1 FROM auth_api_access_token_scopes WHERE token_id IN (?, ?)",
            (alice_t1, alice_t2),
        )
        is None
    )
    assert (
        db.fetch_one(
            "SELECT 1 FROM auth_api_access_token_events WHERE token_id IN (?, ?)",
            (alice_t1, alice_t2),
        )
        is None
    )
    assert (
        db.fetch_one("SELECT 1 FROM auth_passkey_challenges WHERE user_id = ?", (aid,))
        is None
    )
    assert result["credentials"] >= 4  # 2 tokens + scope + event + challenge

    # The other subject's credentials are preserved verbatim.
    assert (
        db.fetch_one(
            "SELECT token_encrypted FROM auth_api_access_tokens WHERE id = ?",
            (bob_t,),
        )["token_encrypted"]
        == f"ENCRYPTED-TOKEN-{bob_t}"
    )
    assert (
        db.fetch_one("SELECT 1 FROM auth_passkey_challenges WHERE user_id = ?", (bid,))
        is not None
    )


def test_sweep_orphans_reaps_credentials_of_deleted_accounts(
    db, auth_manager, pii_gen, monkeypatch
):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    _ensure_credential_child_tables(db)
    orphan_owner = 9_900_000_001
    _api_token(db, 9_900_000_002, orphan_owner)
    # No auth_users row for orphan_owner, so it is a genuine orphan.

    swept = sweep_orphans(db)
    assert orphan_owner in swept["credentials"]
    assert (
        db.fetch_one(
            "SELECT 1 FROM auth_api_access_tokens WHERE created_by = ?",
            (orphan_owner,),
        )
        is None
    )


def test_purge_user_credentials_missing_tables_is_tolerated(db):
    # A partial/older schema lacking the optional credential tables must not
    # abort the rest of an erasure; lack of tables is not an error.
    assert purge_user_credentials(db, 12_345) >= 0
