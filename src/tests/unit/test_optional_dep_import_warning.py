"""Regression: import-time optional-dependency warnings must not crash.

``src/core/auth/passkeys.py`` and
``src/core/voice/signaling/sfu/aiortc_adapter.py`` emit ``logger.warning``
at module import time when their optional dependency is missing. The app
logger is only configured in ``setup_logging()``, so on a fresh interpreter
— or any import before setup — that warning raised
``RuntimeError("Logger not configured...")`` and the import itself failed.
The warnings are best-effort (guarded), so the modules import cleanly and
report ``*_AVAILABLE = False`` instead.
"""

import importlib
import sys

import pytest


def _disable_logger(real_logger):
    """Simulate an import before ``setup_logging()``; prove it took effect."""
    real_logger._setup_called = False
    with pytest.raises(RuntimeError):
        real_logger.warning("precondition: unconfigured logger raises")


@pytest.mark.unit
class TestOptionalDependencyImportWarnings:
    def test_passkeys_import_without_webauthn_and_logger(self, monkeypatch):
        from src.utils.common_utils.utils import logger as real_logger
        import src.core.auth.passkeys as passkeys

        saved_flag = real_logger._setup_called
        monkeypatch.setitem(sys.modules, "webauthn", None)
        try:
            _disable_logger(real_logger)
            reloaded = importlib.reload(passkeys)
            assert reloaded.WEBAUTHN_AVAILABLE is False
        finally:
            real_logger._setup_called = saved_flag
            monkeypatch.undo()
            importlib.reload(passkeys)

    def test_aiortc_adapter_import_without_aiortc_and_logger(self, monkeypatch):
        from src.utils.common_utils.utils import logger as real_logger
        import src.core.voice.signaling.sfu.aiortc_adapter as adapter

        saved_flag = real_logger._setup_called
        monkeypatch.setitem(sys.modules, "aiortc", None)
        monkeypatch.setitem(sys.modules, "aiortc.contrib", None)
        monkeypatch.setitem(sys.modules, "aiortc.contrib.media", None)
        try:
            _disable_logger(real_logger)
            reloaded = importlib.reload(adapter)
            assert reloaded.AIORTC_AVAILABLE is False
            assert reloaded.AIORTC_RECORDING_AVAILABLE is False
        finally:
            real_logger._setup_called = saved_flag
            monkeypatch.undo()
            importlib.reload(adapter)
