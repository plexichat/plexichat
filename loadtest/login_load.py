"""Scenario 01: login (Argon2) throughput/latency. Measures server-side verify cost path.
Use preprovisioned users (PL_USER_PREFIX/COUNT) or a single user via PL_LOGIN_USER.

Usage:
  PL_BASE_URL=http://host:8000/api/v1 PL_USER_COUNT=20 PL_CONCURRENCY=4 PL_DURATION=30 \
    .venv/bin/python loadtest/login_load.py
"""

import asyncio
import json
import os
import time
import statistics
import aiohttp
from config import (
    BASE_URL,
    USER_PREFIX,
    USER_PASSWORD,
    USER_COUNT,
    TOKEN_MODE_BEARER,
    RESULTS_DIR,
)

CONCURRENCY = int(os.environ.get("PL_CONCURRENCY", "4"))
DURATION = float(os.environ.get("PL_DURATION", "30"))
RAMP = float(os.environ.get("PL_RAMP", "0"))
SINGLE_USER = os.environ.get("PL_LOGIN_USER", "")


def pct(v, p):
    if not v:
        return None
    v = sorted(v)
    return v[min(int(len(v) * p / 100), len(v) - 1)]


async def worker(name, session, stats, stop):
    i = 0
    while not stop.is_set():
        user = SINGLE_USER or f"{USER_PREFIX}{(i % max(USER_COUNT, 1)) + 1}"
        i += 1
        body = {"username": user, "password": USER_PASSWORD}
        t0 = time.perf_counter()
        err = None
        status = 0
        try:
            h = {"Content-Type": "application/json"}
            if TOKEN_MODE_BEARER:
                h["X-Plexichat-Token-Mode"] = "bearer"
            async with session.post(
                f"{BASE_URL}/auth/login", json=body, headers=h
            ) as r:
                status = r.status
                await r.read()
        except Exception as e:
            err = str(e)
        dt = (time.perf_counter() - t0) * 1000
        stats["lat_ms"].append(dt)
        stats["total"] += 1
        if err or status >= 400:
            stats["err"] += 1
        else:
            stats["ok"] += 1


async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stats = {"lat_ms": [], "ok": 0, "err": 0, "total": 0}
    stop = asyncio.Event()
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(
        timeout=timeout, connector=aiohttp.TCPConnector(limit=CONCURRENCY + 5)
    ) as s:
        tasks = [
            asyncio.create_task(worker(f"w{n}", s, stats, stop))
            for n in range(CONCURRENCY)
        ]
        t0 = time.time()
        await asyncio.sleep(DURATION)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - t0
    l = stats["lat_ms"]
    res = {
        "scenario": "login",
        "base_url": BASE_URL,
        "concurrency": CONCURRENCY,
        "duration_s": round(elapsed, 2),
        "total": stats["total"],
        "ok": stats["ok"],
        "err": stats["err"],
        "throughput_rps": round(stats["ok"] / elapsed, 3) if elapsed else None,
        "lat_ms": {
            "mean": round(statistics.mean(l), 2) if l else None,
            "p50": round(pct(l, 50), 2) if l else None,
            "p95": round(pct(l, 95), 2) if l else None,
            "p99": round(pct(l, 99), 2) if l else None,
            "max": round(max(l), 2) if l else None,
        },
    }
    out = os.path.join(RESULTS_DIR, "login.json")
    json.dump(res, open(out, "w"), indent=2)
    print(json.dumps(res, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
