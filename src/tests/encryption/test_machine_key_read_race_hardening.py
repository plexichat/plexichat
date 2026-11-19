"""Regression: loading the machine KEK must not have a check/use (TOCTOU) gap.

The old loader validated the key path with ``stat()``/``chmod()`` and then read
the bytes in a separate ``Path.read_bytes()`` open. A local attacker with write
access to the key directory could swap the file for a symlink between the two
operations and make the server load an attacker-chosen KEK (which protects
every keyring). The fix opens once with ``O_NOFOLLOW`` and validates the opened
descriptor via ``fstat``/``fchmod``.
"""

import os
import stat

import pytest

from src.utils.encryption import vault as vault_module
from src.utils.encryption.vault import machine_key


@pytest.fixture(autouse=True)
def _clear_cache():
    loader = getattr(vault_module, "_load_machine_key_cached", None)
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()
    yield
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()


@pytest.mark.unit
def test_symbolic_link_is_refused_at_open(tmp_path):
    """The reader validates the opened fd, so a link at read time is fatal."""
    key_file = tmp_path / "key"
    key_file.symlink_to(tmp_path / "missing")  # dangling link

    with pytest.raises(RuntimeError, match="Unsafe machine key file"):
        machine_key._read_safe_existing_key_file(key_file)


@pytest.mark.unit
def test_non_regular_file_is_refused(tmp_path):
    d = tmp_path / "dirkey"
    d.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        machine_key._read_safe_existing_key_file(d)


@pytest.mark.unit
def test_symlink_swapped_between_check_and_read_is_not_followed(tmp_path, monkeypatch):
    """The attacker-controlled file must never become the loaded KEK.

    We force the swap at the moment of the real I/O so the test reproduces the
    race deterministically: both the legacy ``Path.read_bytes`` path and the
    hardened ``os.open`` path are intercepted.
    """
    key_file = tmp_path / "key"
    real_key = os.urandom(32)
    key_file.write_bytes(real_key)
    os.chmod(key_file, 0o600)

    attacker = tmp_path / "attacker"
    attacker_key = os.urandom(32)
    attacker.write_bytes(attacker_key)

    swapped = {"done": False}

    def maybe_swap(path):
        if not swapped["done"] and str(path) == str(key_file):
            swapped["done"] = True
            key_file.unlink()
            key_file.symlink_to(attacker)

    real_read_bytes = type(key_file).read_bytes

    def patched_read_bytes(self):
        maybe_swap(self)
        return real_read_bytes(self)

    real_os_open = machine_key.os.open

    def patched_os_open(path, flags, *args, **kwargs):
        maybe_swap(path)
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(type(key_file), "read_bytes", patched_read_bytes)
    monkeypatch.setattr(machine_key.os, "open", patched_os_open)

    # The hardened loader opens with O_NOFOLLOW and refuses the swapped link;
    # it must not silently load the attacker's bytes or write over the link.
    with pytest.raises(RuntimeError, match="Unsafe machine key file"):
        vault_module._load_machine_key_impl(str(key_file))

    with attacker.open("rb") as fh:
        assert fh.read() == attacker_key
    assert key_file.is_symlink()


@pytest.mark.unit
def test_regular_key_file_is_read_intact(tmp_path):
    key_file = tmp_path / "key"
    key = os.urandom(32)
    key_file.write_bytes(key)
    os.chmod(key_file, 0o600)

    assert machine_key._read_safe_existing_key_file(key_file) == key
    assert vault_module._load_machine_key_impl(str(key_file)) == key
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
