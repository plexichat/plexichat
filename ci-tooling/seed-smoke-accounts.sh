#!/usr/bin/env bash
#
# Seed the accounts ci-tooling/live-smoke.sh keeps, from inside the deployment.
#
# The gate logs in to `smoke_gate_a`, `smoke_gate_b` and `smoke_gate_c` and only
# registers them when they are missing, so a rerun costs three logins instead of
# three registrations against a per-IP limit (3/minute, 10/hour, 20/day). The
# first run normally creates them itself. When the IP the gate runs from has
# already spent that budget -- a shared CI egress, or a gate run a few times in
# an hour -- it cannot do even that first step, and this creates them through the
# app's own auth manager (which is not rate limited) instead: one command on the
# deployment, once, and every later gate run is login-only. `smoke_gate_c` is
# the run's stranger -- the account that must NOT be able to read A's channel or
# DM -- so seeding it is what keeps those checks from skipping.
#
# It also owns the deletion ring (`smoke_delete_1..4`), and this is the only
# place that ring is refilled. `DELETE /users/@me` schedules deletion with a
# grace period rather than erasing the account: it freezes the account and kills
# its sessions, and a frozen account cannot authenticate, so the run that
# watches a member freeze spends it. Seeding creates a member that is missing
# and restores one an earlier run froze (cancelling the scheduled deletion), so
# running this again heals the whole ring. Run it before an on-demand gate run
# when the ring is low.
#
# It can also mint session tokens for the three gate accounts (--tokens). The
# gate logs in over HTTP, and login is limited per client IP (5/minute and
# 20/hour per route), so a runner sharing its egress with every other job can
# be answered 429 -- and then the release gate is red for a reason that says
# nothing about the deployment. A pre-issued token makes a gate run perform no
# logins at all. Mint once, store the three values as masked CI variables
# (SMOKE_TOKEN_A/B/C), and rotate them when a run logs C out or when the session
# expires. Each mint is a session, so do not loop it: it counts against the
# account's session cap.
#
# Usage, on the deployment host:
#   bash ci-tooling/seed-smoke-accounts.sh
#   bash ci-tooling/seed-smoke-accounts.sh --tokens
#   PLEXICHAT_CONTAINER=plexichat-backend-1 bash ci-tooling/seed-smoke-accounts.sh
#
# Or from a checkout on any machine that can reach the host:
#   scp ci-tooling/seed-smoke-accounts.sh host:/tmp/seed.sh
#   ssh host 'bash /tmp/seed.sh'
#
# SMOKE_PASSWORD must match the one the gate uses (default SmokeTest123!, which
# is also the gate's default).
#
set -uo pipefail

CONTAINER="${PLEXICHAT_CONTAINER:-plexichat-backend-1}"
PASSWORD="${SMOKE_PASSWORD:-SmokeTest123!}"

PYTHON_CODE="$(cat <<'PY'
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/src")

import utils.config as config
import utils.logger as logger

PASSWORD = os.environ["SEED_SMOKE_PASSWORD"]
NAMES = [n for n in os.environ["SEED_SMOKE_NAMES"].split(",") if n]
RESTORE = [n for n in os.environ.get("SEED_RESTORE_NAMES", "").split(",") if n]


def bootstrap():
    from src.bootstrap.utilities import setup_utilities
    from src.config_defaults import get_default_config
    from src.server.config_loader import _apply_env_overrides
    from src.server.lifecycle import VERSION

    config.setup(
        config_path=os.environ.get("PLEXICHAT_CONFIG", "/app/config/docker-config.yaml"),
        default_config=get_default_config(version=VERSION),
    )
    _apply_env_overrides()
    logger.setup(log_dir="/tmp/seed-smoke-logs", level="ERROR", rotate=False)
    setup_utilities()


def main():
    bootstrap()
    from src.core import auth as auth_module
    from src.core.auth.manager import AuthManager
    from src.core.database import Database

    db = Database()
    db.connect()
    auth_module.setup(db)
    manager = AuthManager.get_instance()

    failures = 0
    for username in NAMES:
        existing = manager.get_user_by_username(username)
        if existing is not None:
            print(f"  exists  {username} ({existing.id})")
            continue
        try:
            manager.register(
                username=username,
                email=f"{username}@example.com",
                password=PASSWORD,
            )
        except Exception as exc:  # already there, or refused: report, do not guess
            print(f"  FAILED  {username}: {exc}")
            failures += 1
            continue
        user = manager.get_user_by_username(username)
        if user is None:
            print(f"  FAILED  {username}: registered but not readable")
            failures += 1
        else:
            print(f"  created {username} ({user.id})")

    # Session tokens for the gate's three accounts. `manager.login` is the
    # manager the HTTP login route calls, so the token is a normal session
    # token; the difference is that minting it here never touches the per-IP
    # route limit the gate keeps hitting from a shared egress.
    if os.environ.get("SEED_SMOKE_TOKENS") == "1":
        labels = "ABC"
        gate = [n for n in os.environ.get("SEED_GATE_NAMES", "").split(",") if n]
        for index, username in enumerate(gate[: len(labels)]):
            try:
                result = manager.login(username, PASSWORD)
            except Exception as exc:
                print(f"  FAILED  token for {username}: {exc}")
                failures += 1
                continue
            token = getattr(result, "token", None)
            if not token:
                print(f"  FAILED  token for {username}: login returned no token")
                failures += 1
                continue
            print(f"SMOKE_TOKEN_{labels[index]}={token}")

    # A member frozen by a gate run is still an account, and cancelling the
    # scheduled deletion is what puts it back in the ring. Only `frozen` is
    # touched: an `active` member needs nothing, and a `purged` one is gone.
    for username in RESTORE:
        user = manager.get_user_by_username(username)
        if user is None:
            continue
        if getattr(user, "deletion_status", "active") != "frozen":
            continue
        try:
            manager.cancel_account_deletion(user.id)
        except Exception as exc:
            print(f"  FAILED  restore {username}: {exc}")
            failures += 1
            continue
        print(f"  restored {username} ({user.id})")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
PY
)"

run_python() {
    # Inside the backend container the imports are already at /app; elsewhere
    # hand the script to the running container so it uses the app's own auth
    # manager and database configuration.
    if [ -f /app/src/core/auth/manager.py ]; then
        SEED_SMOKE_PASSWORD="$PASSWORD" SEED_SMOKE_NAMES="$NAMES" \
            SEED_RESTORE_NAMES="$RING" SEED_SMOKE_TOKENS="$TOKENS" \
            SEED_GATE_NAMES="$GATE" python -
        return $?
    fi
    if ! command -v docker >/dev/null 2>&1; then
        echo "ERROR: run this inside the backend container, or on a host with docker." >&2
        return 2
    fi
    docker exec -i \
        -e SEED_SMOKE_PASSWORD="$PASSWORD" \
        -e SEED_SMOKE_NAMES="$NAMES" \
        -e SEED_RESTORE_NAMES="$RING" \
        -e SEED_SMOKE_TOKENS="$TOKENS" \
        -e SEED_GATE_NAMES="$GATE" \
        "$CONTAINER" python - || return $?
}

# The ring the gate's deletion coverage consumes; the gate's own default, so
# both sides agree without an environment variable.
RING="${SMOKE_DELETION_ACCOUNTS:-smoke_delete_1,smoke_delete_2,smoke_delete_3,smoke_delete_4}"
GATE="${SMOKE_GATE_ACCOUNTS:-smoke_gate_a,smoke_gate_b,smoke_gate_c}"
NAMES="$GATE,$RING"

TOKENS=0
case "${1:-}" in
    --tokens) TOKENS=1 ;;
    "") ;;
    *) echo "usage: $0 [--tokens]" >&2; exit 2 ;;
esac

echo "== seeding the live gate's accounts (${NAMES}) =="
printf '%s\n' "$PYTHON_CODE" | run_python
status=$?
if [ "$status" -ne 0 ]; then
    echo "ERROR: could not seed every gate account." >&2
    exit "$status"
fi
if [ "$TOKENS" = 1 ]; then
    echo "  tokens above are credentials: store them as MASKED CI variables"
    echo "  SMOKE_TOKEN_A/B/C on the project that runs the live_smoke job."
fi
echo "  done: the live gate can now log in instead of registering."
