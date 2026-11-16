"""Focused tests for deferred settings-config validation.

Before: SettingsManager compiled the charset regex and read config invariants
in __init__, paying that cost even for managers that were set up but never used
(app startup, test fixtures). After: validation is deferred to the first
validating operation behind a double-checked one-time guard, so it runs exactly
once and invalid config surfaces at first use rather than at construction.
"""

import threading

import pytest

from src.core.settings import manager as settings_manager
from src.core.settings.manager import SettingsManager
from src.core.settings.models import SettingsConfig


class _ReProxy:
    """Proxy that counts compile() calls without touching the global re module."""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def compile(self, *args, **kwargs):
        self.calls += 1
        return self._real.compile(*args, **kwargs)


def test_construction_does_not_compile_or_validate(monkeypatch):
    proxy = _ReProxy(settings_manager.re)
    monkeypatch.setattr(settings_manager, "re", proxy)

    manager = SettingsManager(None, SettingsConfig())

    assert manager._config_validated is False
    assert manager._key_pattern is None
    assert proxy.calls == 0


def test_first_use_validates_exactly_once(monkeypatch):
    proxy = _ReProxy(settings_manager.re)
    monkeypatch.setattr(settings_manager, "re", proxy)
    manager = SettingsManager(None, SettingsConfig())

    manager._validate_key("theme")
    manager._validate_key("other-key")
    manager._is_readable_key("legacy")

    assert manager._config_validated is True
    assert proxy.calls == 1, "charset regex compiled once, not per call"


def test_invalid_config_raises_on_first_use_not_init():
    manager = SettingsManager(None, SettingsConfig(max_key_length=0))
    # Construction succeeds: the expensive validation is deferred.
    assert manager._config_validated is False

    with pytest.raises(ValueError, match="max_key_length"):
        manager._validate_key("theme")


def test_malformed_charset_surfaces_at_first_use():
    manager = SettingsManager(None, SettingsConfig(key_charset="[bad"))

    with pytest.raises(ValueError, match="key_charset"):
        manager._validate_key("theme")


def test_concurrent_first_use_still_validates_once(monkeypatch):
    proxy = _ReProxy(settings_manager.re)
    monkeypatch.setattr(settings_manager, "re", proxy)
    manager = SettingsManager(None, SettingsConfig())
    barrier = threading.Barrier(8)
    errors = []

    def worker():
        try:
            barrier.wait()
            manager._validate_key("theme")
        except Exception as exc:  # pragma: no cover - failure signal
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert proxy.calls == 1
