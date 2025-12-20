#!/usr/bin/env python3
"""
Example JSON Consumer
Demonstrates how to consume the JSON output from type_check_json.py
"""

import json
import subprocess
import sys
from pathlib import Path


def run_type_check_json() -> dict:
    """Run type check and parse JSON output."""
    result = subprocess.run(
        [sys.executable, "scripts/type_check_json.py"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    
    if result.returncode != 0:
        print(f"Error running type check: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    
    return json.loads(result.stdout)


def analyze_report(report: dict):
    """Analyze type check report and print insights."""
    print("=" * 80)
    print("TYPE CHECK ANALYSIS")
    print("=" * 80)
    print()
    
    # Overall stats
    stats = report["aggregated_statistics"]
    print(f"Total Errors: {stats['total_errors']}")
    print(f"Total Warnings: {stats['total_warnings']}")
    print(f"Total Info: {stats['total_info']}")
    print()
    
    # Repository breakdown
    print("REPOSITORY BREAKDOWN:")
    for repo_name, repo_stats in stats["by_repository"].items():
        print(f"  {repo_name}:")
        print(f"    Errors: {repo_stats['errors']}")
        print(f"    Warnings: {repo_stats['warnings']}")
        print(f"    Info: {repo_stats['info']}")
    print()
    
    # Category breakdown
    print("ISSUES BY CATEGORY:")
    sorted_categories = sorted(
        stats["by_category"].items(),
        key=lambda x: x[1],
        reverse=True
    )
    for category, count in sorted_categories:
        print(f"  {category}: {count}")
    print()
    
    # Top hotspot files
    print("TOP 5 HOTSPOT FILES:")
    for i, hotspot in enumerate(stats["hotspot_files"][:5], 1):
        print(f"  {i}. {hotspot['repository']}/{hotspot['file']}")
        print(f"     Total issues: {hotspot['total_issues']}")
        
        # Show top 3 categories for this file
        sorted_cats = sorted(
            hotspot["by_category"].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for cat, count in sorted_cats[:3]:
            print(f"       - {cat}: {count}")
    print()
    
    # Quality score (simple example)
    total_files = len(set(
        f"{repo['name']}/{issue['file']}"
        for repo in report["repositories"]
        for category_issues in repo["categories"].values()
        for issue in category_issues
    ))
    
    if total_files > 0:
        avg_issues_per_file = (
            stats["total_errors"] + stats["total_warnings"]
        ) / total_files
        
        print("QUALITY METRICS:")
        print(f"  Files with issues: {total_files}")
        print(f"  Average issues per file: {avg_issues_per_file:.2f}")
        
        # Simple quality score (0-100, higher is better)
        # This is just an example - adjust thresholds for your needs
        if avg_issues_per_file == 0:
            quality_score = 100
        elif avg_issues_per_file < 1:
            quality_score = 90
        elif avg_issues_per_file < 3:
            quality_score = 70
        elif avg_issues_per_file < 5:
            quality_score = 50
        else:
            quality_score = max(0, 50 - (avg_issues_per_file - 5) * 5)
        
        print(f"  Quality Score: {quality_score:.0f}/100")
        print()


def filter_by_category(report: dict, category: str) -> list[dict]:
    """Extract all issues of a specific category."""
    issues = []
    for repo in report["repositories"]:
        for cat, cat_issues in repo["categories"].items():
            if cat == category:
                for issue in cat_issues:
                    issues.append({
                        "repository": repo["name"],
                        **issue
                    })
    return issues


def filter_by_file(report: dict, file_pattern: str) -> list[dict]:
    """Extract all issues for files matching a pattern."""
    issues = []
    for repo in report["repositories"]:
        for category, cat_issues in repo["categories"].items():
            for issue in cat_issues:
                if file_pattern in issue["file"]:
                    issues.append({
                        "repository": repo["name"],
                        "category": category,
                        **issue
                    })
    return issues


def check_thresholds(report: dict, max_errors: int = 0, max_warnings: int = 10) -> bool:
    """Check if report passes quality thresholds (useful for CI/CD)."""
    stats = report["aggregated_statistics"]
    
    if stats["total_errors"] > max_errors:
        print(f"❌ FAILED: {stats['total_errors']} errors (max: {max_errors})")
        return False
    
    if stats["total_warnings"] > max_warnings:
        print(f"⚠️  WARNING: {stats['total_warnings']} warnings (max: {max_warnings})")
        # Warnings don't fail the build in this example
    
    print(f"✅ PASSED: {stats['total_errors']} errors, {stats['total_warnings']} warnings")
    return True


def main():
    """Main entry point."""
    print("Running type check and generating JSON report...")
    print()
    
    report = run_type_check_json()
    
    # Basic analysis
    analyze_report(report)
    
    # Example: Get all Optional issues
    print("EXAMPLE: Optional Issues")
    optional_issues = filter_by_category(report, "Optional")
    print(f"Found {len(optional_issues)} Optional issues")
    if optional_issues:
        print("Sample:")
        for issue in optional_issues[:3]:
            print(f"  {issue['repository']}/{issue['file']}:{issue['line']} - {issue['message'][:60]}...")
    print()
    
    # Example: Get all issues in auth-related files
    print("EXAMPLE: Auth-related Files")
    auth_issues = filter_by_file(report, "auth")
    print(f"Found {len(auth_issues)} issues in auth-related files")
    if auth_issues:
        print("Sample:")
        for issue in auth_issues[:3]:
            print(f"  {issue['file']}:{issue['line']} [{issue['category']}] - {issue['message'][:60]}...")
    print()
    
    # Example: CI/CD threshold check
    print("EXAMPLE: CI/CD Threshold Check")
    passed = check_thresholds(report, max_errors=0, max_warnings=50)
    
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
