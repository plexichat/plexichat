"""Regression: ``_check_port_available`` must not depend on the logger.

It runs in the CLI bootstrap BEFORE ``setup_logging()``, so calling
``logger.error()`` there raised ``RuntimeError("Logger not configured...")``
instead of exiting(1) with the "already in use" message on an occupied
port. The check now reports to stderr directly.
"""

import socket

import pytest


def _occupy():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock


@pytest.mark.unit
class TestCheckPortAvailable:
    def test_free_port_returns_quietly(self):
        from src.cli.main import _check_port_available

        sock = _occupy()
        port = sock.getsockname()[1]
        sock.close()
        assert _check_port_available("127.0.0.1", port) is None

    def test_occupied_port_exits_one_without_configured_logger(self, capsys):
        from src.cli.main import _check_port_available
        from src.utils.common_utils.utils import logger as real_logger

        saved = real_logger._setup_called
        real_logger._setup_called = False
        try:
            with _occupy() as sock:
                with pytest.raises(SystemExit) as exc:
                    _check_port_available("127.0.0.1", sock.getsockname()[1])
                assert exc.value.code == 1
        finally:
            real_logger._setup_called = saved
        assert "already in use" in capsys.readouterr().err
