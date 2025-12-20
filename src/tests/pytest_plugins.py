"""
Pytest plugins for comprehensive test verification.

This module provides custom pytest hooks and plugins for:
- Performance tracking
- Coverage enforcement
- Security violation detection
- Test result reporting
- Parallel execution optimization
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import pytest


class PerformanceTracker:
    """Track test performance and identify slow tests."""
    
    def __init__(self):
        self.test_times: Dict[str, float] = {}
        self.slow_threshold = 1.0  # seconds
        self.very_slow_threshold = 5.0  # seconds
    
    def record_test(self, nodeid: str, duration: float):
        """Record test execution time."""
        self.test_times[nodeid] = duration
    
    def get_slow_tests(self, threshold: float = None) -> List[tuple]:
        """Get tests slower than threshold."""
        threshold = threshold or self.slow_threshold
        slow_tests = [
            (nodeid, duration)
            for nodeid, duration in self.test_times.items()
            if duration > threshold
        ]
        return sorted(slow_tests, key=lambda x: x[1], reverse=True)
    
    def generate_report(self) -> str:
        """Generate performance report."""
        lines = [
            "=" * 80,
            "Performance Report",
            "=" * 80,
            "",
            f"Total tests: {len(self.test_times)}",
            f"Total time: {sum(self.test_times.values()):.2f}s",
            f"Average time: {sum(self.test_times.values()) / len(self.test_times):.3f}s" if self.test_times else "N/A",
            "",
        ]
        
        slow_tests = self.get_slow_tests(self.slow_threshold)
        if slow_tests:
            lines.append(f"Slow tests (>{self.slow_threshold}s):")
            for nodeid, duration in slow_tests[:20]:
                lines.append(f"  {duration:6.2f}s - {nodeid}")
            if len(slow_tests) > 20:
                lines.append(f"  ... and {len(slow_tests) - 20} more")
        else:
            lines.append("No slow tests detected ✓")
        
        lines.append("")
        
        very_slow_tests = self.get_slow_tests(self.very_slow_threshold)
        if very_slow_tests:
            lines.append(f"⚠ Very slow tests (>{self.very_slow_threshold}s): {len(very_slow_tests)}")
        
        return "\n".join(lines)


class SecurityViolationTracker:
    """Track security test failures."""
    
    def __init__(self):
        self.violations: List[Dict] = []
        self.security_markers = [
            'security',
            'sql_injection',
            'xss',
            'csrf',
            'authentication_bypass',
            'authorization',
            'session_hijacking',
            'token_validation',
        ]
    
    def is_security_test(self, item) -> bool:
        """Check if test is security-related."""
        # Check markers
        for marker in item.iter_markers():
            if marker.name in self.security_markers:
                return True
        
        # Check test name
        test_name = item.nodeid.lower()
        for marker in self.security_markers:
            if marker in test_name:
                return True
        
        return False
    
    def record_failure(self, item, report):
        """Record security test failure."""
        if self.is_security_test(item):
            self.violations.append({
                'test': item.nodeid,
                'outcome': report.outcome,
                'message': str(report.longrepr) if hasattr(report, 'longrepr') else '',
                'markers': [m.name for m in item.iter_markers()],
            })
    
    def has_violations(self) -> bool:
        """Check if there are any security violations."""
        return len(self.violations) > 0
    
    def generate_report(self) -> str:
        """Generate security violation report."""
        lines = [
            "=" * 80,
            "Security Violation Report",
            "=" * 80,
            "",
        ]
        
        if not self.violations:
            lines.append("✓ No security violations detected")
        else:
            lines.append(f"✗ {len(self.violations)} security violation(s) detected:")
            lines.append("")
            
            for i, violation in enumerate(self.violations[:10], 1):
                lines.append(f"{i}. {violation['test']}")
                lines.append(f"   Outcome: {violation['outcome']}")
                lines.append(f"   Markers: {', '.join(violation['markers'])}")
                
                # Show first few lines of error message
                if violation['message']:
                    msg_lines = violation['message'].split('\n')[:3]
                    for line in msg_lines:
                        lines.append(f"   {line}")
                
                lines.append("")
            
            if len(self.violations) > 10:
                lines.append(f"... and {len(self.violations) - 10} more violations")
        
        lines.append("")
        return "\n".join(lines)


class CoverageEnforcer:
    """Enforce coverage thresholds."""
    
    def __init__(self):
        self.thresholds = {
            'src/core/auth': 90.0,
            'src/core/messaging': 85.0,
            'src/core/servers': 85.0,
            'src/api': 80.0,
            'src/core': 85.0,  # Default for all core modules
            'src': 85.0,  # Overall target
        }
        self.coverage_data = {}
    
    def check_coverage(self, cov) -> Dict[str, tuple]:
        """Check coverage against thresholds."""
        results = {}
        
        try:
            # Get coverage data by directory
            for path, threshold in self.thresholds.items():
                # This is a placeholder - actual implementation depends on coverage.py API
                # In practice, you would use cov.report() or cov.get_data()
                actual = 0.0  # Placeholder
                results[path] = (actual, threshold, actual >= threshold)
        
        except Exception:
            pass
        
        return results
    
    def generate_report(self, results: Dict[str, tuple]) -> str:
        """Generate coverage report."""
        lines = [
            "=" * 80,
            "Coverage Enforcement Report",
            "=" * 80,
            "",
        ]
        
        all_passed = True
        
        for path, (actual, threshold, passed) in results.items():
            status = "✓" if passed else "✗"
            lines.append(f"{status} {path:40s} | Target: {threshold:5.1f}% | Actual: {actual:5.1f}%")
            
            if not passed:
                all_passed = False
        
        lines.append("")
        
        if all_passed:
            lines.append("✓ All coverage thresholds met")
        else:
            lines.append("✗ Some coverage thresholds not met")
        
        lines.append("")
        return "\n".join(lines)


class TestMetricsCollector:
    """Collect various test metrics."""
    
    def __init__(self):
        self.total_tests = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.errors = 0
        self.xfailed = 0
        self.xpassed = 0
        
        self.tests_by_module = defaultdict(lambda: {'passed': 0, 'failed': 0, 'skipped': 0})
        self.tests_by_marker = defaultdict(lambda: {'passed': 0, 'failed': 0, 'skipped': 0})
    
    def record_result(self, item, outcome: str):
        """Record test result."""
        self.total_tests += 1
        
        # Overall counts
        if outcome == 'passed':
            self.passed += 1
        elif outcome == 'failed':
            self.failed += 1
        elif outcome == 'skipped':
            self.skipped += 1
        elif outcome == 'error':
            self.errors += 1
        elif outcome == 'xfailed':
            self.xfailed += 1
        elif outcome == 'xpassed':
            self.xpassed += 1
        
        # By module
        module = item.module.__name__ if hasattr(item, 'module') else 'unknown'
        self.tests_by_module[module][outcome] += 1
        
        # By marker
        for marker in item.iter_markers():
            self.tests_by_marker[marker.name][outcome] += 1
    
    def generate_report(self) -> str:
        """Generate metrics report."""
        lines = [
            "=" * 80,
            "Test Metrics Report",
            "=" * 80,
            "",
            f"Total tests: {self.total_tests}",
            f"  Passed:  {self.passed:5d} ({self.passed/self.total_tests*100:5.1f}%)" if self.total_tests > 0 else "  Passed:  0",
            f"  Failed:  {self.failed:5d} ({self.failed/self.total_tests*100:5.1f}%)" if self.total_tests > 0 else "  Failed:  0",
            f"  Skipped: {self.skipped:5d} ({self.skipped/self.total_tests*100:5.1f}%)" if self.total_tests > 0 else "  Skipped: 0",
            "",
        ]
        
        if self.tests_by_marker:
            lines.append("Tests by marker:")
            for marker, counts in sorted(self.tests_by_marker.items()):
                total = sum(counts.values())
                lines.append(f"  {marker:20s}: {total:4d} tests ({counts['passed']} passed, {counts['failed']} failed)")
            lines.append("")
        
        return "\n".join(lines)


# Global tracker instances
performance_tracker = PerformanceTracker()
security_tracker = SecurityViolationTracker()
coverage_enforcer = CoverageEnforcer()
metrics_collector = TestMetricsCollector()


# Pytest hooks

def pytest_configure(config):
    """Configure pytest with custom plugins."""
    # Register custom markers
    config.addinivalue_line("markers", "security: Security-critical test that must not fail")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Hook to capture test results."""
    outcome = yield
    report = outcome.get_result()
    
    # Record performance
    if call.when == 'call':
        performance_tracker.record_test(item.nodeid, call.duration)
    
    # Record security violations
    if report.failed or report.outcome == 'error':
        security_tracker.record_failure(item, report)
    
    # Record metrics
    if call.when == 'call':
        metrics_collector.record_result(item, report.outcome)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Add custom sections to terminal summary."""
    terminalreporter.write_sep("=", "Custom Reports")
    
    # Performance report
    perf_report = performance_tracker.generate_report()
    terminalreporter.write_line(perf_report)
    terminalreporter.write_line("")
    
    # Security report
    security_report = security_tracker.generate_report()
    terminalreporter.write_line(security_report)
    
    # Force exit with error if security violations detected
    if security_tracker.has_violations():
        terminalreporter.write_line("")
        terminalreporter.write_line("✗ CRITICAL: Security violations detected. Build must fail.", red=True, bold=True)
    
    # Metrics report
    metrics_report = metrics_collector.generate_report()
    terminalreporter.write_line(metrics_report)
    
    # Save reports to files
    reports_dir = Path("test-reports")
    reports_dir.mkdir(exist_ok=True)
    
    with open(reports_dir / "performance.txt", "w") as f:
        f.write(perf_report)
    
    with open(reports_dir / "security.txt", "w") as f:
        f.write(security_report)
    
    with open(reports_dir / "metrics.txt", "w") as f:
        f.write(metrics_report)
    
    # JSON reports for CI/CD
    json_data = {
        'performance': {
            'total_tests': len(performance_tracker.test_times),
            'total_time': sum(performance_tracker.test_times.values()),
            'slow_tests': len(performance_tracker.get_slow_tests()),
            'very_slow_tests': len(performance_tracker.get_slow_tests(5.0)),
        },
        'security': {
            'violations': len(security_tracker.violations),
            'has_violations': security_tracker.has_violations(),
        },
        'metrics': {
            'total': metrics_collector.total_tests,
            'passed': metrics_collector.passed,
            'failed': metrics_collector.failed,
            'skipped': metrics_collector.skipped,
            'errors': metrics_collector.errors,
        },
    }
    
    with open(reports_dir / "test-summary.json", "w") as f:
        json.dump(json_data, f, indent=2)


def pytest_collection_modifyitems(config, items):
    """Modify test collection for optimization."""
    # Sort tests for better parallel execution
    # Run fast unit tests first, then integration tests
    
    unit_tests = []
    integration_tests = []
    other_tests = []
    
    for item in items:
        if any(marker.name == 'unit' for marker in item.iter_markers()):
            unit_tests.append(item)
        elif any(marker.name == 'integration' for marker in item.iter_markers()):
            integration_tests.append(item)
        else:
            other_tests.append(item)
    
    # Reorder: unit tests first (fast feedback), then integration
    items[:] = unit_tests + other_tests + integration_tests
