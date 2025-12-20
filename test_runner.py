#!/usr/bin/env python3
"""
Comprehensive test runner with multiple modes.

Usage:
    python test_runner.py              # Run all tests
    python test_runner.py --fast       # Run only fast tests
    python test_runner.py --security   # Run only security tests
    python test_runner.py --module auth # Run specific module
    python test_runner.py --coverage   # Run with coverage
"""

import argparse
import subprocess
import sys


def run_pytest(args_list):
    """Run pytest with given arguments."""
    cmd = ["pytest"] + args_list
    print(f"Running: {' '.join(cmd)}")
    print("-" * 80)
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description="Run PlexiChat tests")
    
    # Test selection
    parser.add_argument("--fast", action="store_true", help="Run only fast tests (unit)")
    parser.add_argument("--slow", action="store_true", help="Include slow tests")
    parser.add_argument("--security", action="store_true", help="Run only security tests")
    parser.add_argument("--module", "-m", help="Run specific module (auth, messaging, etc.)")
    parser.add_argument("--marker", help="Run tests with specific marker")
    parser.add_argument("--path", help="Run tests in specific path")
    
    # Execution options
    parser.add_argument("--parallel", "-n", action="store_true", help="Run tests in parallel")
    parser.add_argument("--workers", type=int, default=0, help="Number of parallel workers (0=auto)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet output")
    parser.add_argument("--failfast", "-x", action="store_true", help="Stop on first failure")
    
    # Coverage options
    parser.add_argument("--coverage", action="store_true", help="Run with coverage")
    parser.add_argument("--coverage-html", action="store_true", help="Generate HTML coverage report")
    parser.add_argument("--coverage-fail", action="store_true", help="Fail if coverage below threshold")
    
    # Reporting options
    parser.add_argument("--junit", action="store_true", help="Generate JUnit XML report")
    parser.add_argument("--durations", type=int, default=0, help="Show N slowest tests")
    
    args = parser.parse_args()
    
    # Build pytest arguments
    pytest_args = []
    
    # Test path
    if args.path:
        pytest_args.append(args.path)
    else:
        pytest_args.append("src/tests/")
    
    # Test selection
    if args.fast:
        pytest_args.extend(["-m", "unit"])
    elif args.security:
        pytest_args.extend(["-m", "security"])
    elif args.module:
        pytest_args.extend(["-m", args.module])
    elif args.marker:
        pytest_args.extend(["-m", args.marker])
    else:
        # Default: exclude slow tests
        if not args.slow:
            pytest_args.extend(["-m", "not slow"])
    
    # Parallel execution
    if args.parallel:
        pytest_args.extend(["-n", str(args.workers) if args.workers > 0 else "auto"])
    
    # Verbosity
    if args.verbose:
        pytest_args.append("-v")
    elif args.quiet:
        pytest_args.append("-q")
    
    # Fail fast
    if args.failfast:
        pytest_args.append("-x")
    
    # Coverage
    if args.coverage or args.coverage_html or args.coverage_fail:
        pytest_args.extend(["--cov=src", "--cov-report=term-missing"])
        
        if args.coverage_html:
            pytest_args.append("--cov-report=html")
        
        pytest_args.append("--cov-report=xml")
        
        if args.coverage_fail:
            pytest_args.append("--cov-fail-under=85")
    
    # Reporting
    if args.junit:
        pytest_args.append("--junitxml=test-results.xml")
    
    if args.durations > 0:
        pytest_args.extend(["--durations", str(args.durations)])
    
    # Always show short traceback
    pytest_args.append("--tb=short")
    
    # Run tests
    exit_code = run_pytest(pytest_args)
    
    # Print summary
    print()
    print("=" * 80)
    if exit_code == 0:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed")
    print("=" * 80)
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
