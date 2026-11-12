"""Tests for cross-process safety of Keyring writes.

Pipeline #2079's shuffled-order job (four xdist workers, all sharing one
``~/.plexichat/data/message_keyring.json``) reddened with::

    FileNotFoundError: '.../message_keyring.tmp' -> '.../message_keyring.json'

``_with_file_lock`` ignored ``acquire_file_lock``'s return value, and that
helper is non-blocking, so a second worker proceeded without the lock and both
wrote the same temporary file. These tests pin the two properties that made the
race possible.
"""

import os
import sys
import threading

import pytest

from src.utils.encryption.core.keyring import Keyring


@pytest.mark.unit
@pytest.mark.encryption
def test_save_waits_for_the_file_lock(tmp_path):
    """A save must block while another file description holds the lock."""
    fcntl = pytest.importorskip("fcntl")

    path = tmp_path / "system_keyring.json"
    keyring = Keyring(path)
    lock_path = path.with_suffix(".lock")

    holder = open(lock_path, "ab")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    done = threading.Event()

    def save():
        keyring.save()
        done.set()

    thread = threading.Thread(target=save, daemon=True)
    try:
        thread.start()
        assert not done.wait(0.3), "save must wait for the lock, not write without it"
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    thread.join(5)
    assert done.is_set(), "save completes once the lock is released"
    assert path.exists()


@pytest.mark.unit
@pytest.mark.encryption
def test_save_leaves_no_temporary_file_behind(tmp_path):
    """Each write swaps in its own temp file and cleans up after itself."""
    path = tmp_path / "system_keyring.json"
    Keyring(path).save()

    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.unit
@pytest.mark.encryption
@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX file locks")
def test_two_instances_save_concurrently_without_losing_the_swap(tmp_path):
    """Two processes' worth of saves (one instance each) both land.

    Each instance has its own thread lock, so only the file lock stands between
    them; without it they share one temp path and one ``os.replace`` loses.
    """
    path = tmp_path / "message_keyring.json"
    first = Keyring(path)
    second = Keyring(path)
    errors = []

    def save(keyring):
        try:
            for _ in range(5):
                # get_key() generates the first data key and persists it in one
                # locked write, which is the interleaving the workers hit.
                keyring.get_key()
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(kr,)) for kr in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert errors == []
    assert Keyring(path).keys, "the surviving file still parses as a keyring"
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.unit
@pytest.mark.encryption
def test_first_key_is_generated_only_once(tmp_path):
    """Instances that loaded an empty keyring adopt the persisted first key.

    Every xdist worker in a fresh container constructs its keyring before any
    key exists, so each used to generate its own version-1 key and overwrite
    the previous one. Whichever worker lost that race kept a key the file no
    longer contained, and its later encryption was undecryptable by peers
    (``InvalidTag`` on version 1). The generation path re-reads the file under
    the lock, so all instances converge on one key.
    """
    path = tmp_path / "system_keyring.json"
    first = Keyring(path)
    second = Keyring(path)
    assert first.keys == {} and second.keys == {}

    _, first_key = first.get_key()
    _, second_key = second.get_key()

    assert second_key == first_key
    assert Keyring(path).keys[1] == first_key


@pytest.mark.unit
@pytest.mark.encryption
def test_rotate_keeps_versions_added_by_a_peer(tmp_path):
    """Rotating from a stale view must not drop a peer's key version."""
    path = tmp_path / "system_keyring.json"
    first = Keyring(path)
    first.get_key()

    stale = Keyring(path)
    peer = Keyring(path)
    peer.rotate()

    stale.rotate()

    assert set(Keyring(path).keys) == {1, 2, 3}


@pytest.mark.unit
@pytest.mark.encryption
def test_decrypt_recovers_from_a_stale_keyring(tmp_path, monkeypatch):
    """A manager holding a superseded key reloads the keyring and decrypts.

    This is the admin-email failure from pipeline #2530: the user row was
    encrypted with the key currently on disk while the long-lived module
    manager still had the key it generated before a peer rewrote the file.
    """
    from src.utils.encryption.core.manager import EncryptionManager

    def _manager():
        return EncryptionManager(
            argon2_time_cost=1, argon2_memory_cost=8, argon2_parallelism=1
        )

    monkeypatch.setenv("HOME", str(tmp_path))
    stale = _manager()
    peer = _manager()
    blob = peer.encrypt_data("secret@test.local", context="42")

    # Give the stale instance the same version number with different key
    # material, as it would hold after losing the first-key race.
    stale.keyring.keys = {1: os.urandom(32)}
    stale.keyring.current_version = 1

    assert stale.decrypt_data(blob, context="42") == "secret@test.local"

    # Real tampering still fails after the reload.
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        stale.decrypt_data(tampered, context="42")
