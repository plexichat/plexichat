#!/usr/bin/env python3
"""
Test Status Dashboard

Generates a comprehensive dashboard showing test suite status, coverage,
performance metrics, and security violations.

Usage:
    python test_dashboard.py              # Print to console
    python test_dashboard.py --html       # Generate HTML report
    python test_dashboard.py --json       # Generate JSON report
"""

import argparse
import json
import os
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class TestStats:
    total: int
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float


@dataclass
class CoverageStats:
    line_rate: float
    branch_rate: float
    lines_covered: int
    lines_total: int
    branches_covered: int
    branches_total: int


@dataclass
class ModuleStats:
    name: str
    tests: TestStats
    coverage: Optional[CoverageStats]


@dataclass
class DashboardData:
    timestamp: str
    overall: TestStats
    coverage: Optional[CoverageStats]
    modules: List[ModuleStats]
    security_violations: int
    slow_tests: List[tuple]
    performance_issues: List[str]


class TestDashboard:
    def __init__(self):
        self.data = None
    
    def collect_data(self) -> DashboardData:
        """Collect all test data."""
        timestamp = datetime.now().isoformat()
        
        # Parse test results
        overall_stats = self.parse_test_results("test-results.xml")
        
        # Parse coverage
        coverage_stats = self.parse_coverage("coverage.xml")
        
        # Parse module stats
        modules = self.parse_module_stats()
        
        # Parse security violations
        security_violations = self.parse_security_violations()
        
        # Parse performance data
        slow_tests = self.parse_slow_tests()
        performance_issues = self.check_performance_issues()
        
        return DashboardData(
            timestamp=timestamp,
            overall=overall_stats,
            coverage=coverage_stats,
            modules=modules,
            security_violations=security_violations,
            slow_tests=slow_tests,
            performance_issues=performance_issues
        )
    
    def parse_test_results(self, filepath: str) -> TestStats:
        """Parse JUnit XML test results."""
        if not os.path.exists(filepath):
            return TestStats(0, 0, 0, 0, 0, 0.0)
        
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            if root.tag == 'testsuites':
                suites = root.findall('testsuite')
                total = sum(int(s.get('tests', 0)) for s in suites)
                failed = sum(int(s.get('failures', 0)) for s in suites)
                errors = sum(int(s.get('errors', 0)) for s in suites)
                skipped = sum(int(s.get('skipped', 0)) for s in suites)
                duration = sum(float(s.get('time', 0)) for s in suites)
            else:
                total = int(root.get('tests', 0))
                failed = int(root.get('failures', 0))
                errors = int(root.get('errors', 0))
                skipped = int(root.get('skipped', 0))
                duration = float(root.get('time', 0))
            
            passed = total - failed - errors - skipped
            
            return TestStats(total, passed, failed, skipped, errors, duration)
        
        except Exception:
            return TestStats(0, 0, 0, 0, 0, 0.0)
    
    def parse_coverage(self, filepath: str) -> Optional[CoverageStats]:
        """Parse coverage XML."""
        if not os.path.exists(filepath):
            return None
        
        try:
            tree = ET.parse(filepath)
            root = tree.getroot()
            
            line_rate = float(root.get('line-rate', 0))
            branch_rate = float(root.get('branch-rate', 0))
            lines_covered = int(root.get('lines-covered', 0))
            lines_total = int(root.get('lines-valid', 0))
            branches_covered = int(root.get('branches-covered', 0))
            branches_total = int(root.get('branches-valid', 0))
            
            return CoverageStats(
                line_rate, branch_rate,
                lines_covered, lines_total,
                branches_covered, branches_total
            )
        
        except Exception:
            return None
    
    def parse_module_stats(self) -> List[ModuleStats]:
        """Parse per-module statistics."""
        modules = []
        
        # This would parse per-module test results if available
        # For now, return empty list
        
        return modules
    
    def parse_security_violations(self) -> int:
        """Count security violations."""
        report_path = Path("test-reports/security.txt")
        
        if not report_path.exists():
            return 0
        
        try:
            with open(report_path, 'r') as f:
                content = f.read()
            
            if "0 security violation" in content or "No security violations" in content:
                return 0
            
            # Count violation lines
            violations = [line for line in content.split('\n') if '✗' in line and 'violation' in line.lower()]
            return len(violations)
        
        except Exception:
            return 0
    
    def parse_slow_tests(self) -> List[tuple]:
        """Parse slow tests from performance report."""
        report_path = Path("test-reports/performance.txt")
        
        if not report_path.exists():
            return []
        
        try:
            with open(report_path, 'r') as f:
                content = f.read()
            
            slow_tests = []
            in_slow_section = False
            
            for line in content.split('\n'):
                if "Slow tests" in line:
                    in_slow_section = True
                    continue
                
                if in_slow_section and line.strip() and line.strip()[0].isdigit():
                    parts = line.strip().split(' - ')
                    if len(parts) == 2:
                        duration = float(parts[0].strip().replace('s', ''))
                        test_name = parts[1].strip()
                        slow_tests.append((test_name, duration))
            
            return slow_tests[:10]  # Top 10
        
        except Exception:
            return []
    
    def check_performance_issues(self) -> List[str]:
        """Check for performance issues."""
        issues = []
        
        # Check total duration
        if self.data and self.data.overall.duration > 1800:  # 30 minutes
            issues.append(f"Test suite too slow: {self.data.overall.duration/60:.1f} minutes (target: <30 min)")
        
        return issues
    
    def generate_console_report(self, data: DashboardData):
        """Generate console report."""
        print("=" * 80)
        print("TEST SUITE DASHBOARD")
        print("=" * 80)
        print(f"Generated: {data.timestamp}")
        print()
        
        # Overall stats
        print("OVERALL STATISTICS")
        print("-" * 80)
        print(f"Total Tests:    {data.overall.total:>6}")
        print(f"  Passed:       {data.overall.passed:>6} ({data.overall.passed/data.overall.total*100 if data.overall.total > 0 else 0:>5.1f}%)")
        print(f"  Failed:       {data.overall.failed:>6} ({data.overall.failed/data.overall.total*100 if data.overall.total > 0 else 0:>5.1f}%)")
        print(f"  Skipped:      {data.overall.skipped:>6} ({data.overall.skipped/data.overall.total*100 if data.overall.total > 0 else 0:>5.1f}%)")
        print(f"  Errors:       {data.overall.errors:>6}")
        print(f"Duration:       {data.overall.duration:>6.1f}s ({data.overall.duration/60:.1f} minutes)")
        print()
        
        # Coverage
        if data.coverage:
            print("COVERAGE")
            print("-" * 80)
            print(f"Line Coverage:    {data.coverage.line_rate*100:>5.1f}% ({data.coverage.lines_covered}/{data.coverage.lines_total})")
            print(f"Branch Coverage:  {data.coverage.branch_rate*100:>5.1f}% ({data.coverage.branches_covered}/{data.coverage.branches_total})")
            
            target = 85.0
            if data.coverage.line_rate * 100 >= target:
                print(f"Status:           ✓ PASS (target: {target}%)")
            else:
                print(f"Status:           ✗ FAIL (target: {target}%, missing: {target - data.coverage.line_rate*100:.1f}%)")
            print()
        
        # Security
        print("SECURITY")
        print("-" * 80)
        if data.security_violations == 0:
            print("Status:           ✓ No security violations detected")
        else:
            print(f"Status:           ✗ {data.security_violations} security violation(s) detected")
            print("                  See test-reports/security.txt for details")
        print()
        
        # Performance
        print("PERFORMANCE")
        print("-" * 80)
        
        if data.overall.duration < 1800:
            print("Status:           ✓ PASS (<30 minutes)")
        else:
            print(f"Status:           ✗ FAIL (>{data.overall.duration/60:.1f} minutes)")
        
        if data.slow_tests:
            print()
            print("Slowest Tests:")
            for test_name, duration in data.slow_tests[:5]:
                print(f"  {duration:>6.2f}s - {test_name}")
        
        if data.performance_issues:
            print()
            print("Issues:")
            for issue in data.performance_issues:
                print(f"  ✗ {issue}")
        
        print()
        print("=" * 80)
    
    def generate_html_report(self, data: DashboardData, output_path: str = "test-dashboard.html"):
        """Generate HTML report."""
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Test Suite Dashboard</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 20px; box-shadow: 0 0 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }}
        h2 {{ color: #555; border-bottom: 2px solid #ddd; padding-bottom: 5px; margin-top: 30px; }}
        .metric {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .metric-label {{ color: #777; font-size: 14px; }}
        .metric-value {{ color: #333; font-size: 24px; font-weight: bold; }}
        .status-pass {{ color: #4CAF50; }}
        .status-fail {{ color: #f44336; }}
        .progress-bar {{ width: 100%; height: 30px; background: #ddd; border-radius: 5px; overflow: hidden; }}
        .progress-fill {{ height: 100%; background: #4CAF50; text-align: center; line-height: 30px; color: white; font-weight: bold; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
        th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }}
        th {{ background: #f5f5f5; font-weight: bold; }}
        .timestamp {{ color: #999; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Test Suite Dashboard</h1>
        <p class="timestamp">Generated: {data.timestamp}</p>
        
        <h2>Overall Statistics</h2>
        <div class="metric">
            <div class="metric-label">Total Tests</div>
            <div class="metric-value">{data.overall.total}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Passed</div>
            <div class="metric-value status-pass">{data.overall.passed}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Failed</div>
            <div class="metric-value {'status-fail' if data.overall.failed > 0 else ''}">{data.overall.failed}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Duration</div>
            <div class="metric-value">{data.overall.duration/60:.1f}m</div>
        </div>
        
        <h2>Coverage</h2>
"""
        
        if data.coverage:
            coverage_pct = data.coverage.line_rate * 100
            html += f"""
        <div class="progress-bar">
            <div class="progress-fill" style="width: {coverage_pct}%">{coverage_pct:.1f}%</div>
        </div>
        <p>Line Coverage: {data.coverage.lines_covered:,} / {data.coverage.lines_total:,} lines</p>
        <p>Branch Coverage: {data.coverage.branch_rate*100:.1f}%</p>
"""
        
        html += f"""
        <h2>Security</h2>
        <p class="{'status-pass' if data.security_violations == 0 else 'status-fail'}">
            {'✓ No security violations' if data.security_violations == 0 else f'✗ {data.security_violations} violation(s) detected'}
        </p>
        
        <h2>Performance</h2>
        <p>Test suite completed in {data.overall.duration/60:.1f} minutes</p>
"""
        
        if data.slow_tests:
            html += """
        <table>
            <thead>
                <tr>
                    <th>Duration</th>
                    <th>Test</th>
                </tr>
            </thead>
            <tbody>
"""
            for test_name, duration in data.slow_tests:
                html += f"""
                <tr>
                    <td>{duration:.2f}s</td>
                    <td>{test_name}</td>
                </tr>
"""
            html += """
            </tbody>
        </table>
"""
        
        html += """
    </div>
</body>
</html>
"""
        
        with open(output_path, 'w') as f:
            f.write(html)
        
        print(f"HTML report generated: {output_path}")
    
    def generate_json_report(self, data: DashboardData, output_path: str = "test-dashboard.json"):
        """Generate JSON report."""
        json_data = {
            'timestamp': data.timestamp,
            'overall': asdict(data.overall),
            'coverage': asdict(data.coverage) if data.coverage else None,
            'modules': [asdict(m) for m in data.modules],
            'security_violations': data.security_violations,
            'slow_tests': [{'name': name, 'duration': dur} for name, dur in data.slow_tests],
            'performance_issues': data.performance_issues,
        }
        
        with open(output_path, 'w') as f:
            json.dump(json_data, f, indent=2)
        
        print(f"JSON report generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate test dashboard")
    parser.add_argument("--html", action="store_true", help="Generate HTML report")
    parser.add_argument("--json", action="store_true", help="Generate JSON report")
    parser.add_argument("--output", help="Output file path")
    
    args = parser.parse_args()
    
    dashboard = TestDashboard()
    data = dashboard.collect_data()
    dashboard.data = data
    
    if args.html:
        output = args.output or "test-dashboard.html"
        dashboard.generate_html_report(data, output)
    elif args.json:
        output = args.output or "test-dashboard.json"
        dashboard.generate_json_report(data, output)
    else:
        dashboard.generate_console_report(data)


if __name__ == "__main__":
    main()
