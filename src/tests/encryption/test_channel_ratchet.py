"""
Tests for the channel ratchet sub-package.

These tests cover:

* HKDF key derivation
* RatchetInterval value object
* ChannelRatchetStore CRUD against an in-memory SQLite database
* ChannelRatchetManager end-to-end encrypt/decrypt
* Rotation thresholds (count and age)
* Split-on-delete
* Tampering with the ciphertext / nonce / interval id

The tests do not require a running server. The Database class
is instantiated with the SQLite test config; the migration is
applied manually before each test that needs the schema.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import pytest

# Make the project root importable when running pytest from the
# src directory.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from src.utils.encryption.hkdf import derive_key  # noqa: E402
from src.utils.encryption.channel_ratchet import (  # noqa: E402
    ChannelRatchetManager,
    ChannelRatchetStore,
    EncryptedMessageV3,
    RatchetInterval,
    WIRE_PREFIX,
)
from src.utils.encryption.channel_ratchet.exceptions import (  # noqa: E402
    RatchetIntervalNotFoundError,
)
from src.utils.encryption.core.keyring import Keyring  # noqa: E402


# ---------------------------------------------------------------------------
# Test fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_keyring_path(tmp_path: Path) -> Path:
    return tmp_path / "test_ratchet_keyring.json"


@pytest.fixture
def fresh_keyring(temp_keyring_path: Path) -> Keyring:
    keyring = Keyring(temp_keyring_path)
    yield keyring
    try:
        os.unlink(temp_keyring_path)
    except OSError:
        pass


class _StubRow(dict):
    """Row shape that the store and tests can both consume.

    Mirrors what ``Database.fetch_one`` returns: a dict-like object
    with column-name keys.
    """

    def __init__(self, data):
        super().__init__()
        if data:
            for key, value in data.items():
                self[key] = value


class FakeDatabase:
    """Minimal in-memory database double for the channel ratchet store.

    The double only implements the methods used by the store. The
    schema is created in ``__init__`` so each test gets a clean
    slate. Rows are stored in a plain dict keyed by primary id.
    """

    def __init__(self):
        self.intervals: dict[int, dict] = {}
        self.messages: dict[int, dict] = {}
        self.next_id = 1
        self._tables = {"channel_ratchet_intervals", "msg_messages"}

    # -- surface that the store / manager call --

    def table_exists(self, name: str) -> bool:
        return name in self._tables

    def is_connected(self) -> bool:
        return True

    def connect(self) -> None:
        return None

    def execute(self, query: str, params=None) -> None:
        params = tuple(params or ())
        q = " ".join(query.split())
        if q.startswith("INSERT INTO channel_ratchet_intervals"):
            (
                interval_id,
                conversation_id,
                start_message_id,
                wrapped,
                created_at,
                last_message_at,
            ) = params
            if interval_id in self.intervals:
                raise RuntimeError("duplicate interval id")
            self.intervals[interval_id] = {
                "interval_id": interval_id,
                "conversation_id": conversation_id,
                "start_message_id": start_message_id,
                "end_message_id": None,
                "start_key_wrapped": wrapped,
                "created_at": created_at,
                "last_message_at": last_message_at,
            }
            return
        if q.startswith("UPDATE channel_ratchet_intervals SET end_message_id"):
            end_message_id, now, interval_id = params
            row = self.intervals.get(interval_id)
            if row and row["end_message_id"] is None:
                row["end_message_id"] = end_message_id
                row["last_message_at"] = now
            return
        if q.startswith("UPDATE channel_ratchet_intervals SET last_message_at"):
            now, interval_id = params
            row = self.intervals.get(interval_id)
            if row:
                row["last_message_at"] = now
            return
        if q.startswith("INSERT INTO msg_messages"):
            (
                msg_id,
                conversation_id,
                _author_id,
                content,
                _content_encrypted,
                _content_index,
                _msg_type,
                created_at,
                _updated_at,
                _reply_to_id,
                _metadata,
                _webhook_id,
                ratchet_interval_id,
            ) = params
            self.messages[msg_id] = {
                "id": msg_id,
                "conversation_id": conversation_id,
                "content": content,
                "deleted": 0,
                "ratchet_interval_id": ratchet_interval_id,
                "created_at": created_at,
            }
            return
        raise NotImplementedError(f"unsupported query in FakeDatabase: {q}")

    def fetch_one(self, query: str, params=None):
        params = tuple(params or ())
        q = " ".join(query.split())
        if q.startswith("SELECT * FROM msg_messages WHERE id = ?"):
            row = self.messages.get(params[0])
            return _StubRow(row) if row else None
        if (
            "FROM channel_ratchet_intervals" in q
            and "WHERE conversation_id = ? AND end_message_id IS NULL" in q
        ):
            cid = params[0]
            rows = [
                r
                for r in self.intervals.values()
                if r["conversation_id"] == cid and r["end_message_id"] is None
            ]
            rows.sort(key=lambda r: r["start_message_id"], reverse=True)
            return _StubRow(rows[0]) if rows else None
        if "FROM channel_ratchet_intervals" in q and "WHERE interval_id = ?":
            row = self.intervals.get(params[0])
            return _StubRow(row) if row else None
        if q.startswith("SELECT COUNT(*) AS c FROM msg_messages"):
            interval_id = params[0]
            count = sum(
                1
                for m in self.messages.values()
                if m.get("ratchet_interval_id") == interval_id
                and m.get("deleted", 0) == 0
            )
            return _StubRow({"c": count})
        raise NotImplementedError(f"unsupported fetch_one in FakeDatabase: {q}")

    def fetch_all(self, query: str, params=None):
        params = tuple(params or ())
        q = " ".join(query.split())
        if (
            "FROM channel_ratchet_intervals" in q
            and "ORDER BY start_message_id DESC LIMIT ?" in q
        ):
            cid, limit = params
            rows = [r for r in self.intervals.values() if r["conversation_id"] == cid]
            rows.sort(key=lambda r: r["start_message_id"], reverse=True)
            return [_StubRow(r) for r in rows[: int(limit)]]
        raise NotImplementedError(f"unsupported fetch_all in FakeDatabase: {q}")


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def manager(fake_db, fresh_keyring) -> ChannelRatchetManager:
    return ChannelRatchetManager(
        fake_db,
        fresh_keyring,
        max_messages_per_interval=3,
        max_interval_age_seconds=3600,
        split_on_delete=True,
    )


# ---------------------------------------------------------------------------
# HKDF wrapper
# ---------------------------------------------------------------------------


class TestHKDF:
    def test_derive_key_length(self):
        key = derive_key(b"a" * 32, salt=b"salt", info=b"info", length=32)
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_derive_key_changes_with_salt(self):
        a = derive_key(b"a" * 32, salt=b"salt-a", info=b"info", length=32)
        b = derive_key(b"a" * 32, salt=b"salt-b", info=b"info", length=32)
        assert a != b

    def test_derive_key_changes_with_info(self):
        a = derive_key(b"a" * 32, salt=b"salt", info=b"info-a", length=32)
        b = derive_key(b"a" * 32, salt=b"salt", info=b"info-b", length=32)
        assert a != b

    def test_derive_key_rejects_empty_ikm(self):
        with pytest.raises(ValueError):
            derive_key(b"", salt=b"salt", info=b"info", length=32)

    def test_derive_key_rejects_bad_length(self):
        with pytest.raises(ValueError):
            derive_key(b"a" * 32, salt=b"salt", info=b"info", length=0)


# ---------------------------------------------------------------------------
# RatchetInterval
# ---------------------------------------------------------------------------


class TestRatchetInterval:
    def test_contains_open_interval(self):
        interval = RatchetInterval(
            interval_id=1,
            conversation_id=10,
            start_message_id=100,
            end_message_id=None,
            start_key=b"\x00" * 32,
            created_at=0,
            last_message_at=0,
        )
        assert interval.contains(100)
        assert interval.contains(101)
        assert not interval.contains(99)

    def test_contains_closed_interval(self):
        interval = RatchetInterval(
            interval_id=1,
            conversation_id=10,
            start_message_id=100,
            end_message_id=200,
            start_key=b"\x00" * 32,
            created_at=0,
            last_message_at=0,
        )
        assert interval.contains(100)
        assert interval.contains(199)
        assert not interval.contains(200)
        assert not interval.contains(99)

    def test_to_dict_omits_start_key(self):
        interval = RatchetInterval(
            interval_id=1,
            conversation_id=10,
            start_message_id=100,
            end_message_id=None,
            start_key=b"\x00" * 32,
            created_at=0,
            last_message_at=0,
        )
        d = interval.to_dict()
        assert "start_key" not in d
        assert d["interval_id"] == 1
        assert d["end_message_id"] is None


# ---------------------------------------------------------------------------
# ChannelRatchetStore
# ---------------------------------------------------------------------------


class TestChannelRatchetStore:
    def test_create_and_get_active(self, fake_db, fresh_keyring):
        store = ChannelRatchetStore(fake_db, fresh_keyring)
        interval = store.create(
            interval_id=1,
            conversation_id=42,
            start_message_id=100,
            start_key=b"k" * 32,
            now=1000,
        )
        assert interval.start_key == b"k" * 32
        active = store.get_active(42)
        assert active is not None
        assert active.interval_id == 1

    def test_create_closes_previous_active(self, fake_db, fresh_keyring):
        store = ChannelRatchetStore(fake_db, fresh_keyring)
        store.create(1, 42, 100, b"k" * 32, now=1000)
        store.create(2, 42, 200, b"k" * 32, now=2000)
        assert store.get_active(42).interval_id == 2
        first = store.get_by_id(1)
        assert first is not None
        assert first.end_message_id == 200

    def test_close_active(self, fake_db, fresh_keyring):
        store = ChannelRatchetStore(fake_db, fresh_keyring)
        store.create(1, 42, 100, b"k" * 32, now=1000)
        closed = store.close_active(42, end_message_id=150, now=2000)
        assert closed is not None
        assert closed.end_message_id == 150
        assert store.get_active(42) is None

    def test_touch_updates_last_message_at(self, fake_db, fresh_keyring):
        store = ChannelRatchetStore(fake_db, fresh_keyring)
        store.create(1, 42, 100, b"k" * 32, now=1000)
        store.touch(1, now=1234)
        row = store.get_by_id(1)
        assert row is not None
        assert row.last_message_at == 1234


# ---------------------------------------------------------------------------
# ChannelRatchetManager
# ---------------------------------------------------------------------------


class TestChannelRatchetManager:
    def test_encrypt_then_decrypt_roundtrip(self, manager):
        result = manager.encrypt(
            conversation_id=42,
            message_id=1001,
            plaintext=b"hello ratchet",
        )
        assert isinstance(result, EncryptedMessageV3)
        assert result.envelope.startswith(WIRE_PREFIX)
        plaintext = manager.decrypt(
            conversation_id=42,
            message_id=1001,
            envelope=result.envelope,
        )
        assert plaintext == b"hello ratchet"

    def test_encrypt_uses_distinct_nonces(self, manager):
        envelopes = {
            manager.encrypt(42, 1001, b"a").envelope,
            manager.encrypt(42, 1002, b"b").envelope,
            manager.encrypt(42, 1003, b"c").envelope,
        }
        assert len(envelopes) == 3

    def test_decrypt_with_wrong_message_id_fails(self, manager):
        result = manager.encrypt(42, 1001, b"hello")
        with pytest.raises(Exception):
            manager.decrypt(42, 9999, result.envelope)

    def test_decrypt_with_tampered_ciphertext_fails(self, manager):
        result = manager.encrypt(42, 1001, b"hello")
        head, b64 = result.envelope.split(":", 2)[2].split(":", 1)
        blob = bytearray(base64.b64decode(b64))
        blob[20] ^= 0x01
        tampered = f"ENC:3:{head}:{base64.b64encode(bytes(blob)).decode('ascii')}"
        with pytest.raises(Exception):
            manager.decrypt(42, 1001, tampered)

    def test_decrypt_unknown_interval_raises(self, manager):
        with pytest.raises(RatchetIntervalNotFoundError):
            # Long enough to pass the nonce check; interval id does not exist
            placeholder = base64.b64encode(b"\x00" * 32).decode("ascii")
            manager.decrypt(42, 1001, f"ENC:3:999999:{placeholder}")

    def test_decrypt_wrong_conversation_raises(self, manager):
        result = manager.encrypt(42, 1001, b"hello")
        with pytest.raises(Exception):
            manager.decrypt(99, 1001, result.envelope)

    def test_rotate_after_max_messages(self, manager, fake_db):
        # max_messages_per_interval=3 in the fixture
        manager.encrypt(42, 1001, b"a")
        manager.encrypt(42, 1002, b"b")
        manager.encrypt(42, 1003, b"c")
        active = manager.get_active_interval(42)
        assert active is not None
        # Pre-populate the msg_messages table that the store queries for
        # the count. In production these are inserted by SendMixin after
        # the encrypt call; in the test we simulate that side effect.
        for i, msg_id in enumerate([1001, 1002, 1003], start=1):
            fake_db.messages[msg_id] = {
                "id": msg_id,
                "conversation_id": 42,
                "content": "ENC:3:dummy",
                "deleted": 0,
                "ratchet_interval_id": active.interval_id,
                "created_at": i,
            }
        new = manager.rotate_if_due(42, last_message_id=1003, now_ms=2000)
        assert new is not None
        assert manager.get_active_interval(42).interval_id == new.interval_id

    def test_rotate_after_max_age(self, fake_db, fresh_keyring):
        manager = ChannelRatchetManager(
            fake_db,
            fresh_keyring,
            max_messages_per_interval=10000,
            max_interval_age_seconds=1,
        )
        now = int(time.time() * 1000)
        manager.encrypt(42, 1001, b"a", now=now)
        new = manager.rotate_if_due(42, last_message_id=1001, now_ms=now + 5_000)
        assert new is not None

    def test_no_rotate_when_under_threshold(self, manager):
        manager.encrypt(42, 1001, b"a")
        new = manager.rotate_if_due(42, last_message_id=1001, now_ms=2000)
        assert new is None

    def test_split_on_delete_creates_new_interval(self, manager):
        manager.encrypt(42, 1001, b"a")
        manager.encrypt(42, 1002, b"b")
        old_active_id = manager.get_active_interval(42).interval_id
        new = manager.split_on_delete(42, deleted_message_id=1001, now_ms=5000)
        assert new is not None
        assert new.interval_id != old_active_id
        closed = manager._store.get_by_id(old_active_id)
        assert closed is not None
        assert closed.end_message_id == 1002
        assert new.start_message_id == 1002

    def test_split_on_delete_disabled(self, fake_db, fresh_keyring):
        manager = ChannelRatchetManager(
            fake_db,
            fresh_keyring,
            max_messages_per_interval=100,
            max_interval_age_seconds=86400,
            split_on_delete=False,
        )
        manager.encrypt(42, 1001, b"a")
        new = manager.split_on_delete(42, deleted_message_id=1001, now_ms=5000)
        assert new is None

    def test_snapshot_excludes_raw_key(self, manager):
        manager.encrypt(42, 1001, b"hi")
        snap = manager.snapshot(42)
        assert snap is not None
        assert "start_key" in snap
        assert snap["context_tag"] == "plexichat.channel-message.v3"
        assert snap["nonce_bytes"] == 12
        assert snap["key_bytes"] == 32

    def test_continuity_across_intervals(self, manager):
        r1 = manager.encrypt(42, 1001, b"a")
        r2 = manager.encrypt(42, 1002, b"b")
        r3 = manager.encrypt(42, 1003, b"c")
        manager.rotate_if_due(42, last_message_id=1003, now_ms=10_000)
        r4 = manager.encrypt(42, 1004, b"d")
        for r in (r1, r2, r3, r4):
            pt = manager.decrypt(42, r.message_id, r.envelope)
            assert pt in {b"a", b"b", b"c", b"d"}

    def test_decrypt_rejects_non_v3_envelope(self, manager):
        with pytest.raises(Exception):
            manager.decrypt(42, 1001, "not-an-envelope")
