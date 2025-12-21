#!/usr/bin/env python3
"""
Type Check All Repositories - JSON Output
Runs pyright on plexichat, common-utils, and encryption modules,
and outputs results in JSON format for programmatic consumption.
"""

import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class TypeIssue:
    """Represents a single type checking issue."""
    file: str
    line: int
    column: int
    severity: str
    message: str
    rule: str
    category: str


@dataclass
class RepoReport:
    """Report for a single repository."""
    name: str
    path: str
    success: bool
    summary: dict[str, int]
    categories: dict[str, list[dict]]


@dataclass
class AggregatedStatistics:
    """Aggregated statistics across all repositories."""
    total_errors: int
    total_warnings: int
    total_info: int
    by_category: dict[str, int]
    by_repository: dict[str, dict[str, int]]
    hotspot_files: list[dict[str, any]]


@dataclass
class TypeCheckReport:
    """Complete type check report."""
    metadata: dict[str, str]
    repositories: list[dict]
    aggregated_statistics: dict


class TypeCheckOrchestrator:
    """Orchestrates type checking across all repositories."""
    
    CATEGORY_PATTERNS = {
        "Optional": [
            "reportOptionalMemberAccess",
            "reportOptionalSubscript",
            "reportOptionalOperand",
            "reportOptionalCall",
            "reportOptionalIterable",
            "reportOptionalContextManager",
        ],
        "List/Dict": [
            "list[",
            "dict[",
            "List[",
            "Dict[",
            "Sequence[",
            "Mapping[",
            "Collection[",
        ],
        "Return Type": [
            "reportReturnType",
            "Return type",
            "expected return type",
            "is incompatible with return type",
        ],
        "Import": [
            "reportMissingImports",
            "Import",
            "Cannot find",
            "is not defined",
        ],
        "Async": [
            "async",
            "await",
            "Coroutine",
            "Awaitable",
            "AsyncIterator",
            "AsyncGenerator",
        ],
        "Type Annotation": [
            "reportUnknownParameterType",
            "reportUnknownVariableType",
            "reportUnknownMemberType",
            "Unknown",
        ],
        "Argument Type": [
            "reportArgumentType",
            "Argument of type",
            "is incompatible with parameter",
        ],
        "Assignment": [
            "reportAssignmentType",
            "is incompatible with declared type",
            "cannot be assigned to",
        ],
    }
    
    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.reports: list[RepoReport] = []
        
    def categorize_issue(self, message: str, rule: str) -> str:
        """Categorize an issue based on its message and rule."""
        message_lower = message.lower()
        rule_lower = rule.lower()
        
        for category, patterns in self.CATEGORY_PATTERNS.items():
            for pattern in patterns:
                pattern_lower = pattern.lower()
                if pattern_lower in message_lower or pattern_lower in rule_lower:
                    return category
        
        return "Other"
    
    def run_pyright(self, repo_path: Path, config_file: Path) -> tuple[str, int]:
        """Run pyright on a repository and return output and return code."""
        cmd = ["pyright", "--outputjson", "--project", str(config_file)]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=300,
            )
            return result.stdout, result.returncode
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Timeout after 300 seconds"}), 1
        except FileNotFoundError:
            return json.dumps({"error": "pyright not found"}), 1
        except Exception as e:
            return json.dumps({"error": str(e)}), 1
    
    def parse_pyright_output(self, output: str, repo_name: str, repo_path: Path) -> RepoReport:
        """Parse pyright JSON output into a RepoReport."""
        categories: dict[str, list[dict]] = defaultdict(list)
        
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return RepoReport(
                name=repo_name,
                path=str(repo_path),
                success=False,
                summary={"total_errors": 0, "total_warnings": 0, "total_info": 0},
                categories=categories,
            )
        
        if "error" in data:
            return RepoReport(
                name=repo_name,
                path=str(repo_path),
                success=False,
                summary={"total_errors": 0, "total_warnings": 0, "total_info": 0},
                categories=categories,
            )
        
        summary_data = data.get("summary", {})
        summary = {
            "total_errors": summary_data.get("errorCount", 0),
            "total_warnings": summary_data.get("warningCount", 0),
            "total_info": summary_data.get("informationCount", 0),
        }
        
        diagnostics = data.get("generalDiagnostics", [])
        
        for diag in diagnostics:
            file_path = diag.get("file", "unknown")
            try:
                rel_path = Path(file_path).relative_to(repo_path)
            except ValueError:
                rel_path = Path(file_path)
            
            message = diag.get("message", "")
            rule = diag.get("rule", "")
            category = self.categorize_issue(message, rule)
            
            issue = {
                "file": str(rel_path),
                "line": diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                "column": diag.get("range", {}).get("start", {}).get("character", 0) + 1,
                "severity": diag.get("severity", "error"),
                "message": message,
                "rule": rule,
                "category": category,
            }
            
            categories[category].append(issue)
        
        return RepoReport(
            name=repo_name,
            path=str(repo_path),
            success=True,
            summary=summary,
            categories=dict(categories),
        )
    
    def check_repository(self, name: str, path: Path, config_file: Path) -> RepoReport:
        """Check a single repository."""
        if not path.exists():
            return RepoReport(
                name=name,
                path=str(path),
                success=False,
                summary={"total_errors": 0, "total_warnings": 0, "total_info": 0},
                categories={},
            )
        
        output, _ = self.run_pyright(path, config_file)
        return self.parse_pyright_output(output, name, path)
    
    def generate_aggregated_statistics(self) -> dict:
        """Generate aggregated statistics across all repositories."""
        total_errors = 0
        total_warnings = 0
        total_info = 0
        by_category: dict[str, int] = defaultdict(int)
        by_repository: dict[str, dict[str, int]] = {}
        
        for report in self.reports:
            total_errors += report.summary["total_errors"]
            total_warnings += report.summary["total_warnings"]
            total_info += report.summary["total_info"]
            
            by_repository[report.name] = {
                "errors": report.summary["total_errors"],
                "warnings": report.summary["total_warnings"],
                "info": report.summary["total_info"],
            }
            
            for category, issues in report.categories.items():
                by_category[category] += len(issues)
        
        # Calculate hotspot files
        file_issue_counts: dict[str, dict] = defaultdict(lambda: {"total_issues": 0, "by_category": defaultdict(int), "repository": "", "file": ""})
        
        for report in self.reports:
            for category, issues in report.categories.items():
                for issue in issues:
                    file_key = f"{report.name}::{issue['file']}"
                    if not file_issue_counts[file_key]["repository"]:
                        file_issue_counts[file_key]["repository"] = report.name
                        file_issue_counts[file_key]["file"] = issue["file"]
                    file_issue_counts[file_key]["total_issues"] += 1
                    file_issue_counts[file_key]["by_category"][category] += 1
        
        hotspot_files = [
            {
                "repository": data["repository"],
                "file": data["file"],
                "total_issues": data["total_issues"],
                "by_category": dict(data["by_category"]),
            }
            for data in sorted(
                file_issue_counts.values(),
                key=lambda x: x["total_issues"],
                reverse=True
            )[:20]
        ]
        
        return {
            "total_errors": total_errors,
            "total_warnings": total_warnings,
            "total_info": total_info,
            "by_category": dict(by_category),
            "by_repository": by_repository,
            "hotspot_files": hotspot_files,
        }
    
    def run(self) -> dict:
        """Run type checking on all repositories and return JSON report."""
        config_file = self.root_dir / "pyrightconfig.json"
        
        # Check all repositories
        repos = [
            ("plexichat", self.root_dir),
            ("common-utils", self.root_dir / "src" / "utils" / "common-utils"),
            ("encryption", self.root_dir / "src" / "utils" / "encryption"),
        ]
        
        for name, path in repos:
            report = self.check_repository(name, path, config_file)
            self.reports.append(report)
        
        # Build complete report
        report_data = {
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "config_file": str(config_file),
            },
            "repositories": [
                {
                    "name": r.name,
                    "path": r.path,
                    "success": r.success,
                    "summary": r.summary,
                    "categories": r.categories,
                }
                for r in self.reports
            ],
            "aggregated_statistics": self.generate_aggregated_statistics(),
        }
        
        return report_data


def main():
    """Main entry point."""
    root_dir = Path(__file__).parent.parent
    orchestrator = TypeCheckOrchestrator(root_dir)
    report = orchestrator.run()
    
    # Output JSON to stdout
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
