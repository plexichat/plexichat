"""Regression: a machine KEK that cannot be persisted must never be used.

The old loader generated a fresh 32-byte key, tried to write it to disk, and
swallowed any error from the write before returning that in-memory key. That is
a fail-open with two consequences:

* availability/crypto-shredding: the key was never durable, so every later
  start minted a different KEK and made all keyrings unreadable;
* an attacker-controlled write: ``_persist_machine_key`` refuses to write
  through a symlink and raises ``RuntimeError``. Swallowing it let a local
  attacker who won the check/use race keep the process running with an
  ephemeral KEK instead of failing closed.

Both cases must propagate the error so the operator sees it at startup.
"""

import pytest

from src.utils.encryption import vault as vault_module
from src.utils.encryption.vault import machine_key


@pytest.fixture(autouse=True)
def _clear_machine_key_cache():
    loader = getattr(vault_module, "_load_machine_key_cached", None)
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()
    yield
    if loader is not None and hasattr(loader, "cache_clear"):
        loader.cache_clear()


@pytest.mark.unit
def test_symlink_write_refusal_is_not_swallowed(tmp_path, monkeypatch):
    """A symlink guard firing during generation must abort key loading."""

    def refuse_symlink(path, key):
        raise RuntimeError("Refusing to write machine key through symlink")

    monkeypatch.setattr(machine_key, "_persist_machine_key", refuse_symlink)
    key_file = tmp_path / "key"

    with pytest.raises(RuntimeError, match="symlink"):
        vault_module._load_machine_key_impl(str(key_file))
    assert not key_file.exists(), "no key file may be left behind on failure"


@pytest.mark.unit
def test_unwritable_store_fails_closed(tmp_path, monkeypatch):
    """Any persistence failure (not just symlinks) must fail closed."""

    def out_of_space(path, key):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(machine_key, "_persist_machine_key", out_of_space)
    key_file = tmp_path / "key"

    with pytest.raises(OSError):
        vault_module._load_machine_key_impl(str(key_file))
    assert not key_file.exists()


@pytest.mark.unit
def test_successful_persist_still_returns_key(tmp_path):
    """The happy path is unchanged: a durable key is returned and reused."""
    key_file = tmp_path / "key"
    key = vault_module._load_machine_key_impl(str(key_file))

    assert len(key) == 32
    assert key_file.read_bytes() == key
    assert vault_module._load_machine_key_impl(str(key_file)) == key
