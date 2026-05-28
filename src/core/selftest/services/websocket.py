"""
WebSocket test service for SelfTestRunner.

Tests gateway connectivity: HELLO -> IDENTIFY -> READY -> heartbeat.
"""

import time
import json

import websocket
import src.api as api
import utils.logger as logger

from ..context import SelfTestContext


class WebSocketTester:
    """Validates WebSocket gateway handshake and heartbeat."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def test_websocket(self) -> None:
        ws_url = self.ctx.base_url.replace("http", "ws") + "/gateway"
        logger.info(f"Testing WebSocket gateway: {ws_url}")

        start = time.time()
        success = False
        error_msg = None

        try:
            headers = {}
            internal_secret = api.get_internal_secret()
            if internal_secret:
                headers["X-Plexichat-Internal-Secret"] = internal_secret

            ws_conn = websocket.create_connection(ws_url, timeout=5, header=headers)

            hello = json.loads(ws_conn.recv())
            if hello.get("op") != 10:
                error_msg = f"Expected HELLO (op 10), got op {hello.get('op')}"
                ws_conn.close()
            else:
                ws_conn.send(
                    json.dumps(
                        {
                            "op": 2,
                            "d": {
                                "token": self.ctx.token,
                                "intents": 0,
                                "properties": {
                                    "os": "selftest",
                                    "browser": "python",
                                    "device": "selftest",
                                },
                            },
                        }
                    )
                )

                ready = json.loads(ws_conn.recv())
                if ready.get("t") != "READY":
                    error_msg = f"Expected READY, got {ready.get('t')}"
                    ws_conn.close()
                else:
                    ws_conn.send(json.dumps({"op": 1, "d": int(time.time())}))
                    heartbeat_ack = json.loads(ws_conn.recv())
                    if heartbeat_ack.get("op") != 11:
                        error_msg = f"Expected HEARTBEAT_ACK (op 11), got op {heartbeat_ack.get('op')}"
                        ws_conn.close()
                    else:
                        success = True
                        ws_conn.close()
        except Exception as e:
            error_msg = str(e)

        duration = (time.time() - start) * 1000
        self.ctx.results.append(
            {
                "method": "WS",
                "path": "/gateway",
                "status_code": 101 if success else 0,
                "duration_ms": duration,
                "success": success,
                "error": error_msg,
            }
        )

        if success:
            logger.info(f"WebSocket verified successfully ({duration:.1f}ms)")
        else:
            logger.error(f"WebSocket FAILED: {error_msg}")
