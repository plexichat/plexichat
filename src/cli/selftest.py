import sys
import threading
import time
import logging as py_logging
import uvicorn

import utils.logger as logger


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

    time.sleep(2)

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
