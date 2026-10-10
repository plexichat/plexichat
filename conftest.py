"""
Root conftest.py - Ensures Python path is set up before any test imports.

This file MUST be at the project root to ensure paths are configured
before pytest collects and imports test modules.
"""

import os
import sys

# Keep Hypothesis's storage/cache out of the repo root. This conftest is
# imported before pytest configures the Hypothesis plugin, so the variable
# is honoured (it is read lazily on first storage access).
os.environ.setdefault("HYPOTHESIS_STORAGE_DIRECTORY", "test_artifacts/hypothesis")

# Setup paths at import time (before any test collection)
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")

for path in [project_root, src_path, utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)


def pytest_addoption(parser):
    """Pre-register the ``cache_dir`` ini option.

    ``pytest.ini`` sets ``cache_dir`` (keeps caches out of the repo root),
    but that option is owned by pytest's ``cacheprovider`` plugin — running
    with ``-p no:cacheprovider`` makes the valid setting "unknown" and
    emits ``PytestConfigWarning``. Registering it here keeps the option
    known in both modes; when the plugin is enabled it re-registers the
    same option (pytest overwrites duplicates without error) and the
    ``pytest.ini`` value takes effect either way.
    """
    parser.addini("cache_dir", help="Cache directory path", default=".pytest_cache")
    # Registered here (the project-root conftest) rather than in
    # src/tests/conftest.py so the option exists no matter which test tree a
    # run targets; the hook that consumes it lives in
    # src/tests/_conftest/hooks.py.
    parser.addoption(
        "--shuffle-seed",
        type=int,
        default=None,
        metavar="SEED",
        help=(
            "Run test files in a seeded random order (tests within a file keep "
            "their order and stay together, so --dist loadfile still pins a "
            "file to one worker). Any seed yields a reproducible order, so an "
            "order-dependent failure can be replayed with the same seed. "
            "Without this option the default collection order is used."
        ),
    )
