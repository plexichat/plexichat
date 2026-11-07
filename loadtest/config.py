"""Shared config for Plexichat load tests.
Reads env vars; defaults target a local default compose (backend :8000 direct or via nginx).
"""
import os

BASE_URL = os.environ.get("PL_BASE_URL", "http://localhost:8000/api/v1").rstrip("/")
WS_URL = os.environ.get("PL_WS_URL", "ws://localhost:8000/gateway")
# Provisioning
USER_PREFIX = os.environ.get("PL_USER_PREFIX", "lt_")
USER_DOMAIN = os.environ.get("PL_USER_DOMAIN", "loadtest.invalid")
USER_PASSWORD = os.environ.get("PL_USER_PASSWORD", "LoadTest!Passw0rd#2026")
USER_COUNT = int(os.environ.get("PL_USER_COUNT", "50"))
# Target space (filled by provision.py -> results/state.json)
CHANNEL_ID = os.environ.get("PL_CHANNEL_ID", "")
SERVER_ID = os.environ.get("PL_SERVER_ID", "")
# Auth mode for scripts: "bearer" sends X-Plexichat-Token-Mode: bearer on login
TOKEN_MODE_BEARER = os.environ.get("PL_TOKEN_MODE_BEARER", "1") == "1"
# Timing
CONNECT_TIMEOUT = float(os.environ.get("PL_CONNECT_TIMEOUT", "15"))
REQUEST_TIMEOUT = float(os.environ.get("PL_REQUEST_TIMEOUT", "30"))
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
STATE_FILE = os.path.join(RESULTS_DIR, "state.json")

GATEWAY_HEARTBEAT = 1
GATEWAY_IDENTIFY = 2
GATEWAY_HELLO = 10
GATEWAY_HEARTBEAT_ACK = 11
