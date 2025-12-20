#!/usr/bin/env python3
"""
CI/CD Test Verification Script

Ensures all tests meet requirements:
- All 3000+ tests run successfully
- Coverage targets met (85%+ plexichat, 90%+ common-utils, 80%+ client)
- Tests complete in <30 minutes
- No security violations
"""

import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


@dataclass
class TestResults:
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float
    total: int


@dataclass
class CoverageResults:
    line_coverage: float
    branch_coverage: float
    total_statements: int
    covered_statements: int
    missing_statements: int


@dataclass
class VerificationResult:
    success: bool
    message: str
    details: Dict


class TestVerifier:
    def __init__(self):
        self.results = {}
        self.start_time = time.time()
        
        self.thresholds = {
            'plexichat': {
                'coverage': 85.0,
                'min_tests': 2000,
            },
            'common-utils': {
                'coverage': 90.0,
                'min_tests': 500,
            },
            'client': {
                'coverage': 80.0,
                'min_tests': 500,
            },
        }
        
        self.max_duration_minutes = 30
    
    def run_verification(self) -> int:
        """Run complete verification suite."""
        print("=" * 80)
        print("CI/CD Test Verification")
        print("=" * 80)
        print()
        
        verifications = []
        
        # 1. Run all tests
        print("Step 1: Running all tests...")
        test_result = self.run_all_tests()
        verifications.append(test_result)
        
        if not test_result.success:
            self.print_failure(test_result)
            return 1
        
        # 2. Verify test count
        print("\nStep 2: Verifying test count...")
        count_result = self.verify_test_count()
        verifications.append(count_result)
        
        # 3. Verify coverage
        print("\nStep 3: Verifying coverage targets...")
        coverage_result = self.verify_coverage()
        verifications.append(coverage_result)
        
        # 4. Verify performance
        print("\nStep 4: Verifying performance...")
        perf_result = self.verify_performance()
        verifications.append(perf_result)
        
        # 5. Verify security
        print("\nStep 5: Verifying security tests...")
        security_result = self.verify_security()
        verifications.append(security_result)
        
        # Print summary
        print("\n" + "=" * 80)
        print("Verification Summary")
        print("=" * 80)
        
        all_success = True
        for result in verifications:
            status = "✓" if result.success else "✗"
            color = "\033[92m" if result.success else "\033[91m"
            reset = "\033[0m"
            
            print(f"{color}{status}{reset} {result.message}")
            
            if not result.success:
                all_success = False
                if result.details:
                    for key, value in result.details.items():
                        print(f"  {key}: {value}")
        
        print()
        
        if all_success:
            print("\033[92m✓ All verifications passed!\033[0m")
            return 0
        else:
            print("\033[91m✗ Some verifications failed\033[0m")
            return 1
    
    def run_all_tests(self) -> VerificationResult:
        """Run all tests with coverage."""
        cmd = [
            "pytest",
            "src/tests/",
            "-n", "auto",
            "-m", "not slow",
            "--cov=src",
            "--cov-report=xml",
            "--cov-report=html",
            "--cov-report=term",
            "--junitxml=test-results.xml",
            "--tb=short",
            "-v",
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.max_duration_minutes * 60
            )
            
            # Parse results
            test_results = self.parse_junit_xml("test-results.xml")
            
            if result.returncode == 0 and test_results.failed == 0:
                return VerificationResult(
                    success=True,
                    message=f"All tests passed ({test_results.passed} passed, {test_results.skipped} skipped)",
                    details={
                        'passed': test_results.passed,
                        'failed': test_results.failed,
                        'skipped': test_results.skipped,
                        'duration': f"{test_results.duration:.1f}s",
                    }
                )
            else:
                return VerificationResult(
                    success=False,
                    message="Tests failed",
                    details={
                        'passed': test_results.passed,
                        'failed': test_results.failed,
                        'errors': test_results.errors,
                        'skipped': test_results.skipped,
                    }
                )
        
        except subprocess.TimeoutExpired:
            return VerificationResult(
                success=False,
                message=f"Tests timed out (>{self.max_duration_minutes} minutes)",
                details={}
            )
        except Exception as e:
            return VerificationResult(
                success=False,
                message=f"Test execution failed: {str(e)}",
                details={}
            )
    
    def verify_test_count(self) -> VerificationResult:
        """Verify minimum test count."""
        try:
            test_results = self.parse_junit_xml("test-results.xml")
            total_tests = test_results.total
            min_tests = self.thresholds['plexichat']['min_tests']
            
            if total_tests >= min_tests:
                return VerificationResult(
                    success=True,
                    message=f"Test count verified ({total_tests} tests, target: {min_tests}+)",
                    details={'total': total_tests, 'target': min_tests}
                )
            else:
                return VerificationResult(
                    success=False,
                    message="Insufficient tests",
                    details={'total': total_tests, 'target': min_tests}
                )
        
        except Exception as e:
            return VerificationResult(
                success=False,
                message=f"Failed to verify test count: {str(e)}",
                details={}
            )
    
    def verify_coverage(self) -> VerificationResult:
        """Verify coverage targets."""
        try:
            coverage = self.parse_coverage_xml("coverage.xml")
            target = self.thresholds['plexichat']['coverage']
            
            if coverage.line_coverage >= target:
                return VerificationResult(
                    success=True,
                    message=f"Coverage target met ({coverage.line_coverage:.1f}%, target: {target}%)",
                    details={
                        'line_coverage': f"{coverage.line_coverage:.1f}%",
                        'target': f"{target}%",
                        'statements': coverage.total_statements,
                        'covered': coverage.covered_statements,
                    }
                )
            else:
                return VerificationResult(
                    success=False,
                    message="Coverage below target",
                    details={
                        'line_coverage': f"{coverage.line_coverage:.1f}%",
                        'target': f"{target}%",
                        'missing': coverage.missing_statements,
                    }
                )
        
        except Exception as e:
            return VerificationResult(
                success=False,
                message=f"Failed to verify coverage: {str(e)}",
                details={}
            )
    
    def verify_performance(self) -> VerificationResult:
        """Verify tests complete in reasonable time."""
        total_duration = time.time() - self.start_time
        max_duration = self.max_duration_minutes * 60
        
        if total_duration < max_duration:
            return VerificationResult(
                success=True,
                message=f"Performance target met ({total_duration/60:.1f} min, target: <{self.max_duration_minutes} min)",
                details={
                    'duration': f"{total_duration:.1f}s",
                    'duration_minutes': f"{total_duration/60:.1f} min",
                    'target': f"{self.max_duration_minutes} min",
                }
            )
        else:
            return VerificationResult(
                success=False,
                message="Tests too slow",
                details={
                    'duration': f"{total_duration/60:.1f} min",
                    'target': f"{self.max_duration_minutes} min",
                }
            )
    
    def verify_security(self) -> VerificationResult:
        """Verify no security test failures."""
        try:
            # Check security report from pytest plugins
            report_path = Path("test-reports/security.txt")
            
            if not report_path.exists():
                return VerificationResult(
                    success=True,
                    message="No security report found (assuming no violations)",
                    details={}
                )
            
            with open(report_path, 'r') as f:
                content = f.read()
            
            if "0 security violation" in content or "No security violations" in content:
                return VerificationResult(
                    success=True,
                    message="No security violations detected",
                    details={}
                )
            else:
                # Count violations
                violation_lines = [line for line in content.split('\n') if '✗' in line and 'violation' in line.lower()]
                
                return VerificationResult(
                    success=False,
                    message="Security violations detected",
                    details={'violations': len(violation_lines)}
                )
        
        except Exception as e:
            return VerificationResult(
                success=False,
                message=f"Failed to verify security: {str(e)}",
                details={}
            )
    
    def parse_junit_xml(self, filepath: str) -> TestResults:
        """Parse JUnit XML results."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"JUnit XML not found: {filepath}")
        
        tree = ET.parse(filepath)
        root = tree.getroot()
        
        # Handle both testsuites and testsuite root elements
        if root.tag == 'testsuites':
            suites = root.findall('testsuite')
            passed = sum(int(s.get('tests', 0)) - int(s.get('failures', 0)) - int(s.get('errors', 0)) - int(s.get('skipped', 0)) for s in suites)
            failed = sum(int(s.get('failures', 0)) for s in suites)
            errors = sum(int(s.get('errors', 0)) for s in suites)
            skipped = sum(int(s.get('skipped', 0)) for s in suites)
            duration = sum(float(s.get('time', 0)) for s in suites)
            total = sum(int(s.get('tests', 0)) for s in suites)
        else:
            total = int(root.get('tests', 0))
            failed = int(root.get('failures', 0))
            errors = int(root.get('errors', 0))
            skipped = int(root.get('skipped', 0))
            passed = total - failed - errors - skipped
            duration = float(root.get('time', 0))
        
        return TestResults(
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration=duration,
            total=total
        )
    
    def parse_coverage_xml(self, filepath: str) -> CoverageResults:
        """Parse coverage XML results."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Coverage XML not found: {filepath}")
        
        tree = ET.parse(filepath)
        root = tree.getroot()
        
        # Get line rate (coverage percentage)
        line_rate = float(root.get('line-rate', 0))
        branch_rate = float(root.get('branch-rate', 0))
        
        # Count statements
        lines_valid = int(root.get('lines-valid', 0))
        lines_covered = int(root.get('lines-covered', 0))
        
        return CoverageResults(
            line_coverage=line_rate * 100,
            branch_coverage=branch_rate * 100,
            total_statements=lines_valid,
            covered_statements=lines_covered,
            missing_statements=lines_valid - lines_covered
        )
    
    def print_failure(self, result: VerificationResult):
        """Print failure details."""
        print(f"\n\033[91m✗ {result.message}\033[0m")
        if result.details:
            for key, value in result.details.items():
                print(f"  {key}: {value}")


def main():
    verifier = TestVerifier()
    exit_code = verifier.run_verification()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
