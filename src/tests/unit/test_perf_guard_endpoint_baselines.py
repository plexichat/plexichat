"""Regression guard for the 20-50 ms endpoint band.

The self-test that produced ``slow20_50.txt`` measured full HTTP round trips on
one machine.  An in-process ASGI client is both faster and noisier, so the raw
millisecond numbers are not comparable byte-for-byte.  This guard therefore
uses a *cohort-relative* comparison instead of an absolute wall-clock assert:

* every endpoint is warmed up, then timed as the minimum of several samples
  (the minimum is robust to GC pauses and scheduler pre-emption);
* a single scale factor is derived from the median of ``measured / baseline``
  across the whole cohort, which absorbs host speed and shared harness
  overhead;
* an endpoint fails only if it is materially slower *than the rest of the
  cohort* compared with its recorded baseline (baseline + 20% plus slack).

A uniform slowdown (slow CI host) scales every endpoint equally and cancels
out; a single handler that regresses while its peers do not fails the assert.
All knobs are environment-tunable so CI can widen the slack deliberately.

Run with: pytest -m performance -n 0 src/tests/unit/test_perf_guard_endpoint_baselines.py
"""

import os
import statistics
import time

import pytest

pytestmark = pytest.mark.performance


# Endpoints that reproduce deterministically with just the ``test_client`` and
# ``auth_headers`` fixtures (no seeded snowflakes / special data).  Values are
# the recorded self-test baselines in milliseconds from slow20_50.txt.
BASELINE_MS = {
    "/api/v1/applications": 48.90,
    "/api/v1/auth/sessions": 44.74,
    "/api/v1/auth/password-requirements": 44.08,
    "/api/v1/bots/authorized-apps": 43.92,
    "/api/v1/features/onboarding/presets": 42.61,
    "/api/v1/features/bookmarks": 42.05,
    "/api/v1/capabilities": 37.62,
    "/api/v1/bots/directory": 37.25,
    "/api/v1/users/@me": 34.04,
    "/api/v1/suite/home/pinned": 34.29,
    "/api/v1/artifacts": 30.50,
    "/api/v1/suite/home/activity": 27.18,
}

# Tunable from the environment so CI can add slack without editing the test.
_REGRESSION_FACTOR = float(os.environ.get("PERF_GUARD_FACTOR", "1.20"))
_SLACK_MS = float(os.environ.get("PERF_GUARD_SLACK_MS", "8.0"))
_WARMUP = 2
_SAMPLES = 7


def _min_ms(client, headers, path):
    """Warm the route, then return the fastest of several samples in ms."""
    for _ in range(_WARMUP):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.status_code)

    best = float("inf")
    for _ in range(_SAMPLES):
        start = time.perf_counter()
        response = client.get(path, headers=headers)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert response.status_code == 200, (path, response.status_code)
        best = min(best, elapsed_ms)
    return best


def test_endpoint_band_has_no_relative_regression(test_client, auth_headers):
    """No baseline endpoint may drift beyond its cohort-normalised ceiling."""
    measured = {path: _min_ms(test_client, auth_headers, path) for path in BASELINE_MS}

    assert len(measured) >= 6, "baseline cohort is too small to normalise"

    # Scale factor: median is insensitive to a single regressed endpoint.
    ratios = [measured[p] / BASELINE_MS[p] for p in BASELINE_MS]
    scale = statistics.median(ratios)
    assert 0 < scale < float("inf"), f"invalid cohort scale factor: {scale}"

    regressions = []
    for path, baseline in BASELINE_MS.items():
        ceiling = baseline * scale * _REGRESSION_FACTOR + _SLACK_MS
        actual = measured[path]
        if actual > ceiling:
            regressions.append(
                f"{path}: {actual:.1f}ms > {ceiling:.1f}ms "
                f"(baseline {baseline:.1f}ms, cohort scale {scale:.2f})"
            )

    assert not regressions, "performance regressions detected:\n" + "\n".join(
        regressions
    )
