#!/usr/bin/env python
"""
Test runner script with common configurations.

Usage:
    python scripts/run_tests.py [mode]

Modes:
    fast     - Run only unit tests (no database)
    auth     - Run only auth module tests
    full     - Run all tests except slow
    all      - Run all tests including slow
    parallel - Run all tests in parallel
    coverage - Run with coverage report
    profile  - Run with timing profiling
"""

import subprocess
import sys
import os

# Ensure we're in the project root
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)

MODES = {
    "fast": [
        "pytest",
        "-m", "unit",
        "-v",
        "--tb=short",
    ],
    "auth": [
        "pytest",
        "-m", "auth",
        "-v",
        "--tb=short",
    ],
    "messaging": [
        "pytest",
        "-m", "messaging",
        "-v",
        "--tb=short",
    ],
    "servers": [
        "pytest",
        "-m", "servers",
        "-v",
        "--tb=short",
    ],
    "api": [
        "pytest",
        "-m", "api",
        "-v",
        "--tb=short",
    ],
    "full": [
        "pytest",
        "-m", "not slow",
        "-v",
        "--tb=short",
    ],
    "all": [
        "pytest",
        "-v",
        "--tb=short",
    ],
    "parallel": [
        "pytest",
        "-n", "auto",
        "-v",
        "--tb=short",
    ],
    "parallel4": [
        "pytest",
        "-n", "4",
        "-v",
        "--tb=short",
    ],
    "coverage": [
        "pytest",
        "--cov=src",
        "--cov-report=term-missing",
        "--cov-report=html:coverage_report",
        "-v",
    ],
    "profile": [
        "pytest",
        "--durations=50",
        "-v",
    ],
    "quick": [
        "pytest",
        "-x",  # Stop on first failure
        "-q",  # Quiet output
        "--tb=line",
    ],
}


def print_help():
    """Print usage information."""
    print(__doc__)
    print("\nAvailable modes:")
    for mode, cmd in MODES.items():
        print(f"  {mode:12} - {' '.join(cmd)}")


def main():
    if len(sys.argv) < 2:
        mode = "full"
    elif sys.argv[1] in ("-h", "--help", "help"):
        print_help()
        return 0
    else:
        mode = sys.argv[1]
    
    if mode not in MODES:
        print(f"Unknown mode: {mode}")
        print_help()
        return 1
    
    cmd = MODES[mode]
    
    # Add any additional arguments passed
    if len(sys.argv) > 2:
        cmd.extend(sys.argv[2:])
    
    print(f"Running: {' '.join(cmd)}")
    print("-" * 60)
    
    result = subprocess.run(cmd)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
