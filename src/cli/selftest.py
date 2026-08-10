import sys
import threading
import time
import logging as py_logging
import uvicorn

import requests

import utils.logger as logger


def _wait_until_ready(host: str, port: int, timeout: float = 20.0) -> bool:
    """Poll /health until the server responds or the timeout elapses.

    Replaces a fixed startup sleep so cold boots don't race the probe and
    warm boots don't pay an artificial delay.
    """
    url = f"http://{host}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=1).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def handle_selftest(server, host: str, port: int) -> None:
    logger.info("Starting Self-Test Mode...")
    host = "127.0.0.1"

    py_logging.getLogger("AppLogger").setLevel(py_logging.INFO)

    if server.app is None:
        logger.error("Failed to create FastAPI application for self-test")
        sys.exit(1)

    uvi_config = uvicorn.Config(server.app, host=host, port=port, log_level="error")
    uvi_server = uvicorn.Server(uvi_config)

    server_thread = threading.Thread(target=uvi_server.run, daemon=True)
    server_thread.start()

    if not _wait_until_ready(host, port):
        logger.error(f"Server did not become healthy within {20.0:.0f}s - aborting")
        sys.exit(1)

    try:
        from src.core.selftest.runner import SelfTestRunner

        runner = SelfTestRunner(base_url=f"http://{host}:{port}")
        success = runner.run_all()

        uvi_server.should_exit = True
        server.cleanup()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Self-test execution failed: {e}", exc_info=True)
        sys.exit(1)
