"""Provision load-test users and a target server/channel.
Idempotent-ish: registers users (409 treated as exists), creates a server+channel,
saves ids to results/state.json. Run once, off the measured path.

Usage:
  cd /root/code-plexichat/plexichat
  PL_BASE_URL=http://localhost:8000/api/v1 .venv/bin/python loadtest/provision.py
"""

import asyncio
import json
import os
import sys
import time
import aiohttp
from config import (
    BASE_URL,
    USER_PREFIX,
    USER_DOMAIN,
    USER_PASSWORD,
    USER_COUNT,
    TOKEN_MODE_BEARER,
    STATE_FILE,
    RESULTS_DIR,
)

HEADERS_JSON = {"Content-Type": "application/json"}
if TOKEN_MODE_BEARER:
    HEADERS_JSON["X-Plexichat-Token-Mode"] = "bearer"


async def register_one(session, i):
    body = {
        "username": f"{USER_PREFIX}{i}",
        "email": f"{USER_PREFIX}{i}@{USER_DOMAIN}",
        "password": USER_PASSWORD,
        "age_verified": True,
    }
    async with session.post(f"{BASE_URL}/auth/register", json=body) as r:
        txt = await r.text()
        return i, r.status, txt


async def login_one(session, i):
    body = {"username": f"{USER_PREFIX}{i}", "password": USER_PASSWORD}
    async with session.post(f"{BASE_URL}/auth/login", json=body) as r:
        data = await r.json(content_type=None)
        return i, r.status, data


def bearer(data):
    # LoginResponse may carry token when bearer mode honored; else caller must use cookie jar.
    if isinstance(data, dict):
        return data.get("token") or (data.get("data") or {}).get("token")
    return None


async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=120)
    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(
        timeout=timeout, connector=connector, headers=HEADERS_JSON
    ) as s:
        # register (paced - Argon2 heavy on server)
        ok = exists = fail = 0
        for i in range(1, USER_COUNT + 1):
            _, status, _ = await register_one(s, i)
            if status in (200, 201):
                ok += 1
            elif status == 409:
                exists += 1
            else:
                fail += 1
                print(f"register {i} status={status}", file=sys.stderr)
            if i % 10 == 0:
                print(
                    f"registered {i}/{USER_COUNT} ok={ok} exists={exists} fail={fail}"
                )
        print(f"register done: ok={ok} exists={exists} fail={fail}")

        # login user 1 as owner to create server/channel
        _, st, data = await login_one(s, 1)
        if st != 200:
            sys.exit(f"owner login failed: {st} {data}")
        tok = bearer(data)
        ah = dict(HEADERS_JSON)
        if tok:
            ah["Authorization"] = f"Bearer {tok}"
        # If no bearer token, cookie jar in session carries auth; keep using `s` with cookies.

        # create server
        async with s.post(
            f"{BASE_URL}/servers", json={"name": "Loadtest Server"}, headers=ah
        ) as r:
            srv = await r.json(content_type=None)
            if r.status not in (200, 201):
                sys.exit(f"create server failed: {r.status} {srv}")
        server_id = (
            srv.get("id")
            or srv.get("server", {}).get("id")
            or srv.get("data", {}).get("id")
        )
        # create channel
        async with s.post(
            f"{BASE_URL}/servers/{server_id}/channels",
            json={"name": "loadtest", "type": "text"},
            headers=ah,
        ) as r:
            ch = await r.json(content_type=None)
            if r.status not in (200, 201):
                sys.exit(f"create channel failed: {r.status} {ch}")
        channel_id = (
            ch.get("id")
            or ch.get("channel", {}).get("id")
            or ch.get("data", {}).get("id")
        )

        # best-effort: join other users (invite/join endpoints vary by build; record if manual join needed)
        joins = 0
        for i in range(2, USER_COUNT + 1):
            _, st2, d2 = await login_one(s, i)
            if st2 != 200:
                continue
            tok2 = bearer(d2)
            ah2 = dict(HEADERS_JSON)
            if tok2:
                ah2["Authorization"] = f"Bearer {tok2}"
            # attempt common join path; ignore failures (public/server default-join semantics vary)
            for path in (
                f"/servers/{server_id}/join",
                f"/servers/{server_id}/members/@me",
            ):
                async with s.post(f"{BASE_URL}{path}", json={}, headers=ah2) as r:
                    if r.status in (200, 201, 204):
                        joins += 1
                        break
        print(
            f"join attempts done (best-effort): {joins}/{max(USER_COUNT - 1, 0)} succeeded; if 0, add users to server/channel per your build's API"
        )

    state = {
        "base_url": BASE_URL,
        "user_prefix": USER_PREFIX,
        "user_count": USER_COUNT,
        "password_env": "PL_USER_PASSWORD",  # pragma: allowlist secret
        "server_id": str(server_id),
        "channel_id": str(channel_id),
        "provisioned_at": time.time(),
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"wrote {STATE_FILE}: server_id={server_id} channel_id={channel_id}")


if __name__ == "__main__":
    asyncio.run(main())
