#!/usr/bin/env python3
"""
Plexichat Server - Main Entry Point

This is a thin dispatch wrapper. All logic lives under src/.
"""

# pyright: reportAttributeAccessIssue=false
import os
import sys

project_root = os.path.abspath(os.path.dirname(__file__))
src_path = os.path.join(project_root, "src")

for path in [project_root, src_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from src.cli.main import main  # noqa: E402

if __name__ == "__main__":
    main()
