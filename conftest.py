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
