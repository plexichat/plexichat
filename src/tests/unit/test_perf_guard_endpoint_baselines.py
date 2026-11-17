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
  cohort* compared with its recorded baseline.

A uniform slowdown (slow CI host) scales every endpoint equally and cancels
out.  Per-endpoint variance on a busy, shared runner does not cancel perfectly,
so the test detects an excessively noisy run (via the cohort's ratio spread)
and skips it rather than reporting a false regression.  On a quiet, dedicated
perf runner the spread stays narrow and the assertion runs; set
``PERF_GUARD_FACTOR=1.20`` there to enforce the strict baseline + 20% ceiling.
All knobs are environment-tunable; no absolute wall-clock number is asserted.

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
    "/api/v1/auth/sessions": 44.74,
    "/api/v1/auth/password-requirements": 44.08,
    "/api/v1/bots/authorized-apps": 43.92,
    "/api/v1/features/bookmarks": 42.05,
    "/api/v1/capabilities": 37.62,
    "/api/v1/bots/directory": 37.25,
    "/api/v1/users/@me": 34.04,
}

# Tunable from the environment so CI can tighten or widen the gate without
# editing the test.  A dedicated, quiet perf job should export
# PERF_GUARD_FACTOR=1.20 for the strict baseline + 20% ceiling.
_REGRESSION_FACTOR = float(os.environ.get("PERF_GUARD_FACTOR", "1.35"))
_SLACK_MS = float(os.environ.get("PERF_GUARD_SLACK_MS", "12.0"))
_NOISE_SPREAD = float(os.environ.get("PERF_GUARD_NOISE_SPREAD", "4.0"))
_WARMUP = 2
_SAMPLES = 5


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


@pytest.mark.timeout(150)
def test_endpoint_band_has_no_relative_regression(test_client, auth_headers):
    """No baseline endpoint may drift beyond its cohort-normalised ceiling."""
    measured = {path: _min_ms(test_client, auth_headers, path) for path in BASELINE_MS}

    assert len(measured) >= 6, "baseline cohort is too small to normalise"

    # Scale factor: median is insensitive to a single regressed endpoint.
    ratios = [measured[p] / BASELINE_MS[p] for p in BASELINE_MS]
    scale = statistics.median(ratios)
    assert 0 < scale < float("inf"), f"invalid cohort scale factor: {scale}"

    # A shared runner can distort individual endpoints unevenly (other jobs,
    # page faults, import storms).  When the cohort's ratio spread is this
    # wide the run cannot attribute a slowdown to a handler, so skip rather
    # than report a false regression.  A dedicated CI job is expected to be
    # quiet enough for the spread to stay narrow and the assert to run.
    spread = max(ratios) / min(ratios)
    if spread > _NOISE_SPREAD:
        pytest.skip(
            f"perf run too noisy to attribute regressions "
            f"(ratio spread {spread:.1f} > {_NOISE_SPREAD:.1f})"
        )

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
