"""Regression pin: ``soundboard.setup()`` only accepts modules by keyword.

History: the factory once carried ``(db, auth, config)``-shaped positional
slots, and a caller passing ``(db, config)`` positionally silently landed
the config object in the wrong slot. The signature is now keyword-only so
that failure mode raises ``TypeError`` instead of mis-wiring. These tests
pin both halves:

- a positional second argument is rejected (fails on the pre-fix
  signature, which would silently accept it), and
- keyword wiring lands each module in its own manager slot, so a future
  slot reorder cannot silently swap servers/media either.
"""

import pytest

from src.core import soundboard


@pytest.fixture(autouse=True)
def _preserve_soundboard_globals():
    """Save/restore the module globals so these tests never leak a manager
    pinned to this test's database into later suites."""
    manager, complete = soundboard._manager, soundboard._setup_complete
    try:
        yield
    finally:
        soundboard._manager = manager
        soundboard._setup_complete = complete


def test_setup_rejects_positional_modules(db):
    """A positional second argument must raise, never silently mis-slot."""
    with pytest.raises(TypeError):
        soundboard.setup(db, object())  # type: ignore[call-arg]


def test_setup_wires_each_module_into_its_own_slot(db):
    """Keyword wiring must land servers/media in their own manager slots."""
    servers_sentinel = object()
    media_sentinel = object()
    soundboard.setup(db, servers_module=servers_sentinel, media_module=media_sentinel)
    manager = soundboard._manager
    assert manager is not None
    assert manager.db is db
    assert manager._servers is servers_sentinel
    assert manager._media is media_sentinel


def test_setup_defaults_are_unwired(db):
    """Bare ``setup(db)`` still works and leaves optional slots empty."""
    soundboard.setup(db)
    manager = soundboard._manager
    assert manager is not None
    assert manager.db is db
    assert manager._servers is None
    assert manager._media is None
