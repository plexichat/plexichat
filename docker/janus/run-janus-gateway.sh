#!/usr/bin/env bash
#
# Start (or stop) the local Janus gateway that the opt-in media tests need.
#
#   ./run-janus-gateway.sh up      # start it and wait until VideoRoom answers
#   ./run-janus-gateway.sh down    # tear it down (and drop its state)
#
# The waiting is the point: ``docker compose up -d`` returns while Janus is
# still loading its plugins, and the tests fail closed against a gateway that
# cannot answer. This script only reports success once the gateway has
# answered an ``info`` request *with the VideoRoom plugin loaded*, so a green
# "ready" line cannot mean "the container started but the plugin is missing".
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
compose_file="${here}/docker-compose.janus.yml"
# Probing honours the same variable the tests read, so "point me at my
# gateway" works with either name.
api_url="${PLEXICHAT_JANUS_API:-${PLEXICHAT_JANUS_URL:-http://127.0.0.1:8088/janus}}"
wait_seconds="${PLEXICHAT_JANUS_WAIT_S:-60}"

action="${1:-up}"
case "${action}" in
    up) ;;
    down)
        docker compose -f "${compose_file}" down -v
        exit 0
        ;;
    *)
        echo "usage: $(basename "$0") [up|down]" >&2
        exit 2
        ;;
esac

if ! command -v docker >/dev/null 2>&1; then
    echo "docker is required to run the Janus gateway" >&2
    exit 1
fi

docker compose -f "${compose_file}" up -d

echo "waiting for the Janus API at ${api_url} (up to ${wait_seconds}s) ..."

python3 - "${api_url}" "${wait_seconds}" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

url, wait_seconds = sys.argv[1], float(sys.argv[2])
deadline = time.time() + wait_seconds
last = "no response"

while time.time() < deadline:
    request = urllib.request.Request(
        url,
        data=json.dumps({"janus": "info", "transaction": "probe"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
        plugins = payload.get("plugins") or {}
        if "janus.plugin.videoroom" in plugins:
            version = plugins["janus.plugin.videoroom"]
            print(f"ready: Janus answered with VideoRoom {version}")
            print()
            print("Run the gateway tests with:")
            print(
                f"  PLEXICHAT_JANUS_URL={url} "
                "python -m pytest src/tests/voice/test_sfu_janus_gateway.py -q"
            )
            sys.exit(0)
        last = f"answered without the VideoRoom plugin: {sorted(plugins)}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        last = str(exc)
    time.sleep(2)

print(f"Janus did not become ready: {last}", file=sys.stderr)
sys.exit(1)
PY
