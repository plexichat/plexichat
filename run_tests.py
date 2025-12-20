#!/usr/bin/env python3
"""
Comprehensive Test Runner for PlexiChat

Runs all tests across repositories with:
- Coverage reporting (85%+ plexichat, 90%+ common-utils, 80%+ client)
- Performance tracking (<30min total)
- Security violation detection
- Parallel execution
- HTML reports
"""

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class TestResult:
    name: str
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float
    coverage: Optional[float]
    security_violations: List[str]


@dataclass
class CoverageTarget:
    repo: str
    target: float
    path: str


class TestRunner:
    def __init__(self, verbose: bool = False, parallel: bool = True, coverage: bool = True):
        self.verbose = verbose
        self.parallel = parallel
        self.coverage = coverage
        self.results: Dict[str, TestResult] = {}
        self.start_time = time.time()
        
        self.coverage_targets = [
            CoverageTarget("plexichat", 85.0, "src"),
            CoverageTarget("common-utils", 90.0, "src/utils/common-utils"),
            CoverageTarget("client", 80.0, "client"),
        ]
    
    def run_all(self) -> int:
        print("=" * 80)
        print("PlexiChat Comprehensive Test Suite")
        print("=" * 80)
        print()
        
        exit_code = 0
        
        # 1. Run main repository tests
        if not self.run_plexichat_tests():
            exit_code = 1
        
        # 2. Run common-utils tests (submodule)
        if os.path.exists("src/utils/common-utils"):
            if not self.run_submodule_tests("common-utils", "src/utils/common-utils"):
                exit_code = 1
        
        # 3. Run client tests (if exists)
        if os.path.exists("client"):
            if not self.run_client_tests():
                exit_code = 1
        
        # 4. Generate reports
        self.generate_summary_report()
        self.check_coverage_targets()
        self.check_security_violations()
        self.check_performance()
        
        return exit_code
    
    def run_plexichat_tests(self) -> bool:
        print("Running PlexiChat tests...")
        print("-" * 80)
        
        cmd = ["pytest", "src/tests/"]
        
        if self.parallel:
            cmd.extend(["-n", "auto"])
        
        if self.coverage:
            cmd.extend([
                "--cov=src",
                "--cov-report=html:htmlcov",
                "--cov-report=xml:coverage.xml",
                "--cov-report=term-missing",
            ])
        
        cmd.extend([
            "-v" if self.verbose else "-q",
            "--tb=short",
            "--junitxml=test-results.xml",
            "--durations=20",
            "-m", "not slow",  # Skip slow tests by default
        ])
        
        start = time.time()
        result = subprocess.run(cmd, capture_output=not self.verbose)
        duration = time.time() - start
        
        # Parse results
        passed, failed, skipped, errors = self.parse_pytest_output(result.stdout, result.stderr)
        coverage_pct = self.parse_coverage() if self.coverage else None
        
        self.results["plexichat"] = TestResult(
            name="PlexiChat",
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration=duration,
            coverage=coverage_pct,
            security_violations=self.check_security_tests(result),
        )
        
        print(f"✓ PlexiChat tests completed in {duration:.1f}s")
        print()
        
        return result.returncode == 0
    
    def run_submodule_tests(self, name: str, path: str) -> bool:
        print(f"Running {name} tests...")
        print("-" * 80)
        
        if not os.path.exists(path):
            print(f"⚠ {name} not found at {path}, skipping")
            return True
        
        # Check for pytest.ini or tests directory
        has_tests = (
            os.path.exists(os.path.join(path, "pytest.ini")) or
            os.path.exists(os.path.join(path, "tests"))
        )
        
        if not has_tests:
            print(f"⚠ No tests found in {name}, skipping")
            return True
        
        cmd = ["pytest", path]
        
        if self.parallel:
            cmd.extend(["-n", "auto"])
        
        if self.coverage:
            cmd.extend([
                f"--cov={path}",
                f"--cov-report=html:htmlcov/{name}",
                f"--cov-report=xml:coverage-{name}.xml",
                "--cov-report=term-missing",
            ])
        
        cmd.extend([
            "-v" if self.verbose else "-q",
            "--tb=short",
            f"--junitxml=test-results-{name}.xml",
        ])
        
        start = time.time()
        result = subprocess.run(cmd, capture_output=not self.verbose)
        duration = time.time() - start
        
        passed, failed, skipped, errors = self.parse_pytest_output(result.stdout, result.stderr)
        coverage_pct = self.parse_coverage_file(f"coverage-{name}.xml") if self.coverage else None
        
        self.results[name] = TestResult(
            name=name,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration=duration,
            coverage=coverage_pct,
            security_violations=[],
        )
        
        print(f"✓ {name} tests completed in {duration:.1f}s")
        print()
        
        return result.returncode == 0
    
    def run_client_tests(self) -> bool:
        print("Running client tests...")
        print("-" * 80)
        
        # Client is typically JavaScript/TypeScript, so use npm/jest
        if os.path.exists("client/package.json"):
            cmd = ["npm", "test", "--prefix", "client"]
        else:
            print("⚠ No package.json found in client, skipping")
            return True
        
        start = time.time()
        result = subprocess.run(cmd, capture_output=not self.verbose)
        duration = time.time() - start
        
        # Note: This is a placeholder - actual parsing depends on client test framework
        self.results["client"] = TestResult(
            name="Client",
            passed=0,
            failed=0,
            skipped=0,
            errors=0,
            duration=duration,
            coverage=None,
            security_violations=[],
        )
        
        print(f"✓ Client tests completed in {duration:.1f}s")
        print()
        
        return result.returncode == 0
    
    def parse_pytest_output(self, stdout: bytes, stderr: bytes) -> tuple:
        """Parse pytest output to extract test counts."""
        text = (stdout + stderr).decode('utf-8', errors='ignore')
        
        passed = text.count(" PASSED")
        failed = text.count(" FAILED")
        skipped = text.count(" SKIPPED")
        errors = text.count(" ERROR")
        
        # Try to parse from summary line
        for line in text.split('\n'):
            if 'passed' in line.lower():
                parts = line.split()
                for i, part in enumerate(parts):
                    if 'passed' in part.lower() and i > 0:
                        try:
                            passed = int(parts[i-1])
                        except ValueError:
                            pass
                    elif 'failed' in part.lower() and i > 0:
                        try:
                            failed = int(parts[i-1])
                        except ValueError:
                            pass
                    elif 'skipped' in part.lower() and i > 0:
                        try:
                            skipped = int(parts[i-1])
                        except ValueError:
                            pass
                    elif 'error' in part.lower() and i > 0:
                        try:
                            errors = int(parts[i-1])
                        except ValueError:
                            pass
        
        return passed, failed, skipped, errors
    
    def parse_coverage(self) -> Optional[float]:
        """Parse coverage from coverage.xml."""
        return self.parse_coverage_file("coverage.xml")
    
    def parse_coverage_file(self, filename: str) -> Optional[float]:
        """Parse coverage percentage from XML file."""
        if not os.path.exists(filename):
            return None
        
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(filename)
            root = tree.getroot()
            
            # Try to find coverage in the root element
            if 'line-rate' in root.attrib:
                return float(root.attrib['line-rate']) * 100
            
            # Try to find in coverage element
            coverage = root.find('.//coverage')
            if coverage is not None and 'line-rate' in coverage.attrib:
                return float(coverage.attrib['line-rate']) * 100
        
        except Exception:
            pass
        
        return None
    
    def check_security_tests(self, result: subprocess.CompletedProcess) -> List[str]:
        """Check for security test failures."""
        violations = []
        
        if result.returncode != 0:
            text = (result.stdout + result.stderr).decode('utf-8', errors='ignore')
            
            # Check for security-related test failures
            security_markers = [
                'test_sql_injection',
                'test_xss',
                'test_csrf',
                'test_authentication_bypass',
                'test_authorization',
                'test_session_hijacking',
                'test_token_validation',
                'test_security',
            ]
            
            for line in text.split('\n'):
                for marker in security_markers:
                    if marker in line.lower() and ('FAILED' in line or 'ERROR' in line):
                        violations.append(line.strip())
        
        return violations
    
    def generate_summary_report(self):
        """Generate summary report."""
        print()
        print("=" * 80)
        print("Test Summary")
        print("=" * 80)
        print()
        
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        total_errors = 0
        total_duration = 0
        
        for name, result in self.results.items():
            total_passed += result.passed
            total_failed += result.failed
            total_skipped += result.skipped
            total_errors += result.errors
            total_duration += result.duration
            
            status = "✓" if result.failed == 0 and result.errors == 0 else "✗"
            
            print(f"{status} {result.name:20s} | "
                  f"Passed: {result.passed:4d} | "
                  f"Failed: {result.failed:4d} | "
                  f"Skipped: {result.skipped:4d} | "
                  f"Time: {result.duration:6.1f}s")
            
            if result.coverage is not None:
                print(f"  {'':20s}   Coverage: {result.coverage:.1f}%")
        
        print()
        print(f"Total: {total_passed + total_failed + total_skipped} tests, "
              f"{total_failed} failed, {total_errors} errors")
        print(f"Duration: {total_duration:.1f}s ({total_duration/60:.1f} minutes)")
        print()
    
    def check_coverage_targets(self):
        """Check if coverage targets are met."""
        print("=" * 80)
        print("Coverage Targets")
        print("=" * 80)
        print()
        
        all_met = True
        
        for target in self.coverage_targets:
            result = self.results.get(target.repo)
            
            if result is None or result.coverage is None:
                print(f"⚠ {target.repo:20s} | No coverage data available")
                continue
            
            if result.coverage >= target.target:
                status = "✓"
            else:
                status = "✗"
                all_met = False
            
            print(f"{status} {target.repo:20s} | "
                  f"Target: {target.target:5.1f}% | "
                  f"Actual: {result.coverage:5.1f}% | "
                  f"{'PASS' if result.coverage >= target.target else 'FAIL'}")
        
        print()
        
        if all_met:
            print("✓ All coverage targets met!")
        else:
            print("✗ Some coverage targets not met")
        
        print()
    
    def check_security_violations(self):
        """Check for security test violations."""
        print("=" * 80)
        print("Security Violations")
        print("=" * 80)
        print()
        
        total_violations = 0
        
        for name, result in self.results.items():
            if result.security_violations:
                total_violations += len(result.security_violations)
                print(f"✗ {name}: {len(result.security_violations)} security test(s) failed")
                for violation in result.security_violations[:5]:  # Show first 5
                    print(f"  - {violation}")
                if len(result.security_violations) > 5:
                    print(f"  ... and {len(result.security_violations) - 5} more")
        
        if total_violations == 0:
            print("✓ No security violations detected")
        else:
            print()
            print(f"✗ Total: {total_violations} security violations")
        
        print()
    
    def check_performance(self):
        """Check if tests complete in reasonable time."""
        print("=" * 80)
        print("Performance")
        print("=" * 80)
        print()
        
        total_time = time.time() - self.start_time
        target_time = 30 * 60  # 30 minutes
        
        if total_time < target_time:
            status = "✓"
            message = "PASS"
        else:
            status = "✗"
            message = "FAIL"
        
        print(f"{status} Total execution time: {total_time:.1f}s ({total_time/60:.1f} minutes)")
        print(f"  Target: <{target_time/60:.0f} minutes | {message}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Run comprehensive test suite for PlexiChat"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel test execution"
    )
    parser.add_argument(
        "--no-coverage",
        action="store_true",
        help="Disable coverage reporting"
    )
    parser.add_argument(
        "--slow",
        action="store_true",
        help="Include slow tests"
    )
    
    args = parser.parse_args()
    
    runner = TestRunner(
        verbose=args.verbose,
        parallel=not args.no_parallel,
        coverage=not args.no_coverage,
    )
    
    exit_code = runner.run_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
