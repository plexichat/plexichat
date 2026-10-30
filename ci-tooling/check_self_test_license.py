#!/usr/bin/env python3
"""Report whether the licence the self-test will load actually unlocks PlexiJoin.

The self-test jobs exercise the licensed-only PlexiJoin/federation routes for
real only when the server boots with a licence that grants ``plexijoin``. On a
protected pipeline CI supplies a real ``$PLEXICHAT_LICENSE``; when that value
is stale, corrupt, or simply does not carry the feature, the licensing module
still loads it (the env var outranks the on-disk licence) and then fails
validation, so the server silently runs free tier and every gated route
answers 403 -- which the suite records as an EXPECTED DENIAL. The licensed code
paths are then never executed, and a PostgreSQL-only defect in them can reach a
release while CI stays green.

This helper answers the question the job actually needs answered: with the
environment as it stands, does the licence the server would load grant
``plexijoin``? Its verdict is what lets the job decide to keep the CI licence
or replace it with the provisional test licence (``write_test_license.py``).

Exit status:

* ``0`` -- the effective licence validates and grants PlexiJoin; keep it.
* ``1`` -- no licence, an unloadable/invalid one, or one without PlexiJoin;
  the caller must fall back to the provisional test licence.

It loads through ``utils.licensing.setup()`` on purpose, so the answer follows
the same precedence the server uses (explicit env path, env base64, then the
default on-disk locations) instead of guessing at the variable's format.

Usage::

    python ci-tooling/check_self_test_license.py
"""

import sys
import tempfile
from pathlib import Path
from typing import Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
# The repo root itself must be importable so the ``src`` package resolves;
# the two sub-paths mirror the layout ``src.utils.common_utils.utils.licensing``
# expects (same pytest.ini puts on pythonpath).
for _path in (
    REPO_ROOT,
    REPO_ROOT / "src",
    REPO_ROOT / "src" / "utils",
    REPO_ROOT / "src" / "utils" / "common_utils",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import src.utils.common_utils.utils.licensing as licensing  # noqa: E402
import src.utils.common_utils.utils.logger as logger  # noqa: E402

_logger_ready = False


def _ensure_logger() -> None:
    """Configure the process logger once; licensing logs through it.

    ``licensing.setup()`` emits its load/validation messages through
    ``utils.logger``, which refuses to log until ``setup()`` has run (the
    server does that in ``main.py``). Under pytest the shared conftest has
    already configured it, so this only has to cover the standalone CLI case.
    """
    global _logger_ready
    if _logger_ready:
        return
    try:
        logger.setup(
            log_dir=tempfile.mkdtemp(prefix="plexichat-ci-license-"),
            level="WARNING",
            zip_logs=False,
        )
    except (OSError, RuntimeError):
        # The verdict does not depend on logging; never fail the check for it.
        pass
    _logger_ready = True


def effective_plexijoin_available() -> Tuple[bool, str]:
    """Return ``(usable, detail)`` for the licence the server would load now.

    ``usable`` is True only when a licence loads, validates, and grants the
    ``plexijoin`` feature. ``detail`` explains a negative verdict for the job
    log, and is empty when the licence is usable.
    """
    _ensure_logger()
    licensing.setup()

    if not licensing.is_valid():
        source = licensing.get_license_source()
        return False, f"no valid licence loaded (source: {source or 'none'})"

    if not licensing.has_feature("plexijoin", default=False):
        return False, (
            "licence is valid but does not grant PlexiJoin "
            f"(type: {licensing.get_license_type()})"
        )

    return True, ""


def main(argv=None) -> int:
    usable, detail = effective_plexijoin_available()
    if usable:
        print(
            "Self-test licence grants PlexiJoin "
            f"(source: {licensing.get_license_source() or 'default location'})"
        )
        return 0

    print(
        f"Self-test licence is unusable for the licensed endpoints: {detail}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
