"""Read-only liveness probes for status mode.

Dependency-free on purpose (stdlib + ``requests`` only): the remote status
branch runs without app config, and a status tool must report a result even
when application code is broken. No authentication, no mutations, no
fixtures, no test allowances — every request goes through production
middleware exactly as a status page would.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

import requests


def _status_headers_ok(resp: requests.Response) -> bool:
    try:
        headers = resp.headers
        return bool(headers.get("X-RateLimit-Limit")) and bool(
            headers.get("X-RateLimit-Remaining")
        )
    except Exception:
        return False


def _openapi_paths_ok(resp: requests.Response) -> bool:
    try:
        data = resp.json()
        return bool((data or {}).get("paths"))
    except Exception:
        return False


# (label, method, path, extra_validator or None)
STATUS_CHECKS: List[
    tuple[str, str, str, Optional[Callable[[requests.Response], bool]]]
] = [
    ("status_health", "GET", "/health", None),
    ("status_api", "GET", "/api/v1/status", _status_headers_ok),
    ("status_openapi", "GET", "/openapi.json", _openapi_paths_ok),
    ("status_version", "GET", "/api/v1/version", None),
]


def run_status_checks(base_url: str, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """Run every status probe against *base_url*.

    Never raises: transport failures become success=False entries with
    status_code 0, so callers always get a complete result list.
    """
    base = base_url.rstrip("/")
    results: List[Dict[str, Any]] = []
    session = requests.Session()
    try:
        for label, method, path, extra in STATUS_CHECKS:
            start = time.time()
            ok = False
            status = 0
            try:
                resp = session.request(method, base + path, timeout=timeout)
                status = resp.status_code
                ok = status == 200 and (extra(resp) if extra else True)
            except Exception:
                ok = False
            results.append(
                {
                    "method": method,
                    "path": path,
                    "status_code": status,
                    "duration_ms": (time.time() - start) * 1000,
                    "success": ok,
                    "label": label,
                }
            )
    finally:
        try:
            session.close()
        except Exception:
            pass
    return results


def summarize(results: List[Dict[str, Any]]) -> bool:
    """Print a compact status summary. Returns True when all checks passed."""
    total = len(results)
    passed = sum(1 for r in results if r.get("success"))
    print("=" * 60)
    print("STATUS CHECK (read-only, unauthenticated)")
    for r in results:
        state = "PASS" if r.get("success") else "FAIL"
        print(
            f"  [{state}] {r.get('method')} {r.get('path')} "
            f"-> {r.get('status_code')} ({r.get('duration_ms', 0):.1f}ms)"
        )
    print(f"STATUS: {passed}/{total} passed")
    print("  No test allowances active: unauthenticated through production")
    print("  middleware; nothing created, mutated, bypassed, or mocked.")
    print("=" * 60)
    return passed == total and total > 0
