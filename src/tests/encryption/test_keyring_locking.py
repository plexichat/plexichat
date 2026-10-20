"""Tests for cross-process safety of Keyring writes.

Pipeline #2079's shuffled-order job (four xdist workers, all sharing one
``~/.plexichat/data/message_keyring.json``) reddened with::

    FileNotFoundError: '.../message_keyring.tmp' -> '.../message_keyring.json'

``_with_file_lock`` ignored ``acquire_file_lock``'s return value, and that
helper is non-blocking, so a second worker proceeded without the lock and both
wrote the same temporary file. These tests pin the two properties that made the
race possible.
"""

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
