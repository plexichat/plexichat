import sys
import os
import socket
import threading
import time
import logging as py_logging
import uvicorn

import requests

import utils.logger as logger


def _is_port_in_use(host: str, port: int) -> bool:
    """Check if a TCP port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


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


def handle_selftest(
    server,
    host: str,
    port: int,
    unlicensed: bool = False,
    status_only: bool = False,
    base_url: str | None = None,
    purge_residue: bool = False,
) -> None:
    if base_url:
        # Remote status mode: probe a live deployment with read-only
        # checks. No server start, no app imports, no test allowances, no
        # teardown. The probe is dependency-free so it reports a result
        # even when application code is broken.
        from src.cli.status_probe import run_status_checks, summarize

        # NOTE: utils.logger is unusable here (logger.setup runs inside
        # _run_startup, which remote mode skips); the probe prints plainly.
        print(f"Status mode: probing {base_url} (no local server)...")
        sys.exit(0 if summarize(run_status_checks(base_url)) else 1)

    logger.info("Starting Self-Test Mode...")
    host = "127.0.0.1"

    # Signal test-only allowances process-wide (the server runs in-thread
    # here, so it inherits this): SSRF loopback targets for the fake remote
    # federation instance, relaxed security checks, etc. Removed on teardown.
    # Status mode needs none of it: its checks are unauthenticated and
    # allowance-free by construction.
    if not status_only:
        os.environ["PLEXICHAT_SELF_TEST"] = "1"

    if _is_port_in_use(host, port):
        logger.error(f"Port {port} is already in use - cannot start self-test")
        sys.exit(1)

    py_logging.getLogger("AppLogger").setLevel(py_logging.INFO)

    if server.app is None:
        logger.error("Failed to create FastAPI application for self-test")
        sys.exit(1)

    uvi_config = uvicorn.Config(server.app, host=host, port=port, log_level="error")
    uvi_server = uvicorn.Server(uvi_config)

    server_thread = threading.Thread(target=uvi_server.run, daemon=True)
    server_thread.start()

    exit_code = 1
    runner = None
    try:
        if not _wait_until_ready(host, port):
            logger.error(f"Server did not become healthy within {20.0:.0f}s - aborting")
            sys.exit(1)

        from src.core.selftest.runner import SelfTestRunner

        runner = SelfTestRunner(
            base_url=f"http://{host}:{port}",
            unlicensed=unlicensed,
            status_only=status_only,
            purge_residue=purge_residue,
        )
        # The residue check runs on its own (``--purge-residue``): it purges
        # an account, so it is never part of the default suite.
        success = runner.run_purge_residue() if purge_residue else runner.run_all()
        exit_code = 0 if success else 1
    except Exception as e:
        logger.error(f"Self-test execution failed: {e}", exc_info=True)
        exit_code = 1
    finally:
        # Full teardown on EVERY exit path (success, failure, exception):
        # stop uvicorn, join its thread, close self-test HTTP sessions,
        # stop background monitors, and release app resources. Without
        # this, short-lived CLI processes leak threads and connections.
        uvi_server.should_exit = True
        server_thread.join(timeout=10)
        if runner is not None:
            try:
                runner.ctx.close_sessions()
            except Exception:
                pass
            try:
                runner.ram_monitor.stop()
            except Exception:
                pass
        try:
            server.cleanup()
        except Exception:
            pass
        try:
            os.environ.pop("PLEXICHAT_SELF_TEST", None)
        except Exception:
            pass
        sys.exit(exit_code)
