"""
Root conftest.py - Ensures Python path is set up before any test imports.

This file MUST be at the project root to ensure paths are configured
before pytest collects and imports test modules.
"""

import os
import sys

# Setup paths at import time (before any test collection)
project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)
