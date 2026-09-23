"""Regression: import-time optional-dependency warnings must not crash.

``src/core/auth/passkeys.py`` and
``src/core/voice/signaling/sfu/aiortc_adapter.py`` emit ``logger.warning``
at module import time when their optional dependency is missing. The app
logger is only configured in ``setup_logging()``, so on a fresh interpreter
— or any import before setup — that warning raised
``RuntimeError("Logger not configured...")`` and the import itself failed.
The warnings are best-effort (guarded), so the modules import cleanly and
report ``*_AVAILABLE = False`` instead.

The modules are exercised as isolated copies loaded under a throwaway name,
never via ``importlib.reload()`` on the shared module: reloading re-executes
the module in place and rebinds every class it defines, while other test
modules still hold the previous objects. That silently broke ``isinstance``
checks downstream (the SFU voice tests asserting
``isinstance(sender.track, BoundedVideoTrack)`` failed with a
same-name repr whenever this file ran first in the session).
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _disable_logger(real_logger):
    """Simulate an import before ``setup_logging()``; prove it took effect."""
    real_logger._setup_called = False
    with pytest.raises(RuntimeError):
        real_logger.warning("precondition: unconfigured logger raises")


def _load_isolated(live_module: types.ModuleType) -> types.ModuleType:
    """Import a fresh copy of a module without touching the shared one.

    The file backing ``live_module`` is executed under a throwaway dotted
    name inside the same package, so relative imports resolve exactly as
    they do for the real module, while the shared module object — and every
    reference other tests already hold to its classes — stays untouched.
    """
    package = live_module.__name__.rpartition(".")[0]
    probe_name = f"{package}._isolated_import_probe"
    path = Path(live_module.__file__).resolve()
    sys.modules.pop(probe_name, None)
    spec = importlib.util.spec_from_file_location(probe_name, path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    sys.modules[probe_name] = probe
    try:
        spec.loader.exec_module(probe)
    finally:
        sys.modules.pop(probe_name, None)
    return probe


@pytest.mark.unit
class TestOptionalDependencyImportWarnings:
    def test_passkeys_import_without_webauthn_and_logger(self, monkeypatch):
        from src.utils.common_utils.utils import logger as real_logger
        import src.core.auth.passkeys as passkeys

        saved_flag = real_logger._setup_called
        monkeypatch.setitem(sys.modules, "webauthn", None)
        try:
            _disable_logger(real_logger)
            reloaded = _load_isolated(passkeys)
            assert reloaded.WEBAUTHN_AVAILABLE is False
        finally:
            real_logger._setup_called = saved_flag

    def test_aiortc_adapter_import_without_aiortc_and_logger(self, monkeypatch):
        from src.utils.common_utils.utils import logger as real_logger
        import src.core.voice.signaling.sfu.aiortc_adapter as adapter

        saved_flag = real_logger._setup_called
        monkeypatch.setitem(sys.modules, "aiortc", None)
        monkeypatch.setitem(sys.modules, "aiortc.contrib", None)
        monkeypatch.setitem(sys.modules, "aiortc.contrib.media", None)
        try:
            _disable_logger(real_logger)
            reloaded = _load_isolated(adapter)
            assert reloaded.AIORTC_AVAILABLE is False
            assert reloaded.AIORTC_RECORDING_AVAILABLE is False
        finally:
            real_logger._setup_called = saved_flag
