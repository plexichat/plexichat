#!/usr/bin/env python3
"""
Type Check All Repositories
Runs pyright on plexichat, common-utils, and encryption modules,
generates detailed reports, and produces a consolidated summary.
"""

import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class TypeIssue:
    """Represents a single type checking issue."""
    file: str
    line: int
    column: int
    severity: str
    message: str
    rule: str
    category: str = ""


@dataclass
class RepoReport:
    """Report for a single repository."""
    repo_name: str
    repo_path: Path
    total_errors: int = 0
    total_warnings: int = 0
    total_info: int = 0
    issues_by_category: dict[str, list[TypeIssue]] = field(default_factory=lambda: defaultdict(list))
    raw_output: str = ""
    success: bool = False


class TypeCheckOrchestrator:
    """Orchestrates type checking across all repositories."""
    
    # Category classification patterns
    CATEGORY_PATTERNS = {
        "Optional": [
            "reportOptionalMemberAccess",
            "reportOptionalSubscript",
            "reportOptionalOperand",
            "reportOptionalCall",
            "reportOptionalIterable",
            "reportOptionalContextManager",
            "Argument of type",
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
        self.timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        
    def categorize_issue(self, issue: TypeIssue) -> str:
        """Categorize an issue based on its message and rule."""
        message_lower = issue.message.lower()
        rule_lower = issue.rule.lower()
        
        for category, patterns in self.CATEGORY_PATTERNS.items():
            for pattern in patterns:
                pattern_lower = pattern.lower()
                if pattern_lower in message_lower or pattern_lower in rule_lower:
                    return category
        
        return "Other"
    
    def run_pyright(self, repo_path: Path, config_file: Path | None = None) -> tuple[str, int]:
        """Run pyright on a repository and return output and return code."""
        cmd = ["pyright", "--outputjson"]
        
        if config_file:
            cmd.extend(["--project", str(config_file)])
        
        print(f"Running: {' '.join(cmd)} in {repo_path}")
        
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
            return json.dumps({"error": "pyright not found. Install with: npm install -g pyright"}), 1
        except Exception as e:
            return json.dumps({"error": str(e)}), 1
    
    def parse_pyright_output(self, output: str, repo_name: str, repo_path: Path) -> RepoReport:
        """Parse pyright JSON output into a RepoReport."""
        report = RepoReport(repo_name=repo_name, repo_path=repo_path, raw_output=output)
        
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            print(f"Warning: Could not parse JSON output for {repo_name}")
            return report
        
        if "error" in data:
            print(f"Error running pyright for {repo_name}: {data['error']}")
            return report
        
        report.success = True
        
        # Parse summary
        summary = data.get("summary", {})
        report.total_errors = summary.get("errorCount", 0)
        report.total_warnings = summary.get("warningCount", 0)
        report.total_info = summary.get("informationCount", 0)
        
        # Parse diagnostics
        diagnostics = data.get("generalDiagnostics", [])
        
        for diag in diagnostics:
            file_path = diag.get("file", "unknown")
            # Make path relative to repo
            try:
                rel_path = Path(file_path).relative_to(repo_path)
            except ValueError:
                rel_path = Path(file_path)
            
            issue = TypeIssue(
                file=str(rel_path),
                line=diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                column=diag.get("range", {}).get("start", {}).get("character", 0) + 1,
                severity=diag.get("severity", "error"),
                message=diag.get("message", ""),
                rule=diag.get("rule", ""),
            )
            
            # Categorize the issue
            issue.category = self.categorize_issue(issue)
            
            # Add to category
            report.issues_by_category[issue.category].append(issue)
        
        return report
    
    def check_plexichat(self):
        """Check the main plexichat repository."""
        print("\n" + "="*80)
        print("CHECKING: PlexiChat (main repository)")
        print("="*80)
        
        config_file = self.root_dir / "pyrightconfig.json"
        output, returncode = self.run_pyright(self.root_dir, config_file)
        report = self.parse_pyright_output(output, "plexichat", self.root_dir)
        self.reports.append(report)
        
        print(f"Errors: {report.total_errors}, Warnings: {report.total_warnings}, Info: {report.total_info}")
    
    def check_common_utils(self):
        """Check the common-utils submodule."""
        print("\n" + "="*80)
        print("CHECKING: common-utils (submodule)")
        print("="*80)
        
        repo_path = self.root_dir / "src" / "utils" / "common-utils"
        
        if not repo_path.exists():
            print(f"Warning: {repo_path} does not exist. Skipping.")
            return
        
        # Use plexichat's pyrightconfig.json for common-utils
        config_file = self.root_dir / "pyrightconfig.json"
        output, returncode = self.run_pyright(repo_path, config_file)
        report = self.parse_pyright_output(output, "common-utils", repo_path)
        self.reports.append(report)
        
        print(f"Errors: {report.total_errors}, Warnings: {report.total_warnings}, Info: {report.total_info}")
    
    def check_encryption(self):
        """Check the encryption module."""
        print("\n" + "="*80)
        print("CHECKING: encryption (module)")
        print("="*80)
        
        repo_path = self.root_dir / "src" / "utils" / "encryption"
        
        if not repo_path.exists():
            print(f"Warning: {repo_path} does not exist. Skipping.")
            return
        
        # Use plexichat's pyrightconfig.json for encryption
        config_file = self.root_dir / "pyrightconfig.json"
        output, returncode = self.run_pyright(repo_path, config_file)
        report = self.parse_pyright_output(output, "encryption", repo_path)
        self.reports.append(report)
        
        print(f"Errors: {report.total_errors}, Warnings: {report.total_warnings}, Info: {report.total_info}")
    
    def generate_individual_report(self, report: RepoReport) -> str:
        """Generate a detailed markdown report for a single repository."""
        lines = []
        
        lines.append(f"# Type Check Report: {report.repo_name}")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Repository Path:** `{report.repo_path}`")
        lines.append("")
        
        if not report.success:
            lines.append("## ❌ Error")
            lines.append("Type checking failed or could not be completed.")
            lines.append("")
            lines.append("```")
            lines.append(report.raw_output[:1000])
            lines.append("```")
            return "\n".join(lines)
        
        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Total Errors:** {report.total_errors}")
        lines.append(f"- **Total Warnings:** {report.total_warnings}")
        lines.append(f"- **Total Info:** {report.total_info}")
        lines.append("")
        
        # Category breakdown
        if report.issues_by_category:
            lines.append("## Issues by Category")
            lines.append("")
            
            # Sort categories by issue count
            sorted_categories = sorted(
                report.issues_by_category.items(),
                key=lambda x: len(x[1]),
                reverse=True
            )
            
            for category, issues in sorted_categories:
                lines.append(f"### {category} ({len(issues)} issues)")
                lines.append("")
                
                # Group by file
                issues_by_file = defaultdict(list)
                for issue in issues:
                    issues_by_file[issue.file].append(issue)
                
                # Sort files by issue count
                sorted_files = sorted(
                    issues_by_file.items(),
                    key=lambda x: len(x[1]),
                    reverse=True
                )
                
                for file_path, file_issues in sorted_files[:10]:  # Top 10 files
                    lines.append(f"#### `{file_path}` ({len(file_issues)} issues)")
                    lines.append("")
                    
                    for issue in file_issues[:5]:  # Top 5 issues per file
                        severity_icon = {
                            "error": "🔴",
                            "warning": "🟡",
                            "information": "🔵",
                        }.get(issue.severity, "⚪")
                        
                        lines.append(f"{severity_icon} **Line {issue.line}:{issue.column}**")
                        lines.append(f"- {issue.message}")
                        if issue.rule:
                            lines.append(f"- *Rule:* `{issue.rule}`")
                        lines.append("")
                    
                    if len(file_issues) > 5:
                        lines.append(f"*...and {len(file_issues) - 5} more issues in this file*")
                        lines.append("")
                
                if len(sorted_files) > 10:
                    remaining_files = len(sorted_files) - 10
                    remaining_issues = sum(len(issues) for _, issues in sorted_files[10:])
                    lines.append(f"*...and {remaining_issues} more issues in {remaining_files} other files*")
                    lines.append("")
        
        return "\n".join(lines)
    
    def generate_consolidated_summary(self) -> str:
        """Generate a consolidated summary across all repositories."""
        lines = []
        
        lines.append("# Consolidated Type Check Summary")
        lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # Overall statistics
        lines.append("## Overall Statistics")
        lines.append("")
        
        total_errors = sum(r.total_errors for r in self.reports)
        total_warnings = sum(r.total_warnings for r in self.reports)
        total_info = sum(r.total_info for r in self.reports)
        
        lines.append(f"- **Total Errors Across All Repos:** {total_errors}")
        lines.append(f"- **Total Warnings Across All Repos:** {total_warnings}")
        lines.append(f"- **Total Info Across All Repos:** {total_info}")
        lines.append("")
        
        # Per-repository summary
        lines.append("## Repository Breakdown")
        lines.append("")
        lines.append("| Repository | Errors | Warnings | Info | Status |")
        lines.append("|------------|--------|----------|------|--------|")
        
        for report in self.reports:
            status = "✅ Success" if report.success else "❌ Failed"
            lines.append(f"| {report.repo_name} | {report.total_errors} | {report.total_warnings} | {report.total_info} | {status} |")
        
        lines.append("")
        
        # Category aggregation across all repos
        lines.append("## Issues by Category (All Repositories)")
        lines.append("")
        
        all_categories = defaultdict(list)
        for report in self.reports:
            for category, issues in report.issues_by_category.items():
                all_categories[category].extend([(report.repo_name, issue) for issue in issues])
        
        sorted_categories = sorted(
            all_categories.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )
        
        for category, repo_issues in sorted_categories:
            lines.append(f"### {category} ({len(repo_issues)} total)")
            lines.append("")
            
            # Count by repository
            repo_counts = defaultdict(int)
            for repo_name, _ in repo_issues:
                repo_counts[repo_name] += 1
            
            for repo_name, count in sorted(repo_counts.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"- **{repo_name}:** {count} issues")
            
            lines.append("")
        
        # Prioritized fix recommendations
        lines.append("## 🎯 Prioritized Fix Recommendations")
        lines.append("")
        
        lines.append("### Priority 1: Critical Type Safety Issues")
        lines.append("")
        
        # Import errors - highest priority
        import_count = len(all_categories.get("Import", []))
        if import_count > 0:
            lines.append(f"1. **Fix Import Errors ({import_count} issues)**")
            lines.append("   - These prevent proper type checking of dependent code")
            lines.append("   - Verify all imports are correct and modules are available")
            lines.append("   - Check for missing dependencies in requirements.txt")
            lines.append("")
        
        # Return type mismatches
        return_count = len(all_categories.get("Return Type", []))
        if return_count > 0:
            lines.append(f"2. **Fix Return Type Mismatches ({return_count} issues)**")
            lines.append("   - These indicate incorrect function contracts")
            lines.append("   - May cause runtime errors when return values are used")
            lines.append("   - Review function signatures and actual return statements")
            lines.append("")
        
        # Argument type mismatches
        arg_count = len(all_categories.get("Argument Type", []))
        if arg_count > 0:
            lines.append(f"3. **Fix Argument Type Mismatches ({arg_count} issues)**")
            lines.append("   - These indicate incorrect function calls")
            lines.append("   - May cause runtime errors")
            lines.append("   - Review function call sites and parameter types")
            lines.append("")
        
        lines.append("### Priority 2: Optional Handling")
        lines.append("")
        
        optional_count = len(all_categories.get("Optional", []))
        if optional_count > 0:
            lines.append(f"4. **Handle Optional Types ({optional_count} issues)**")
            lines.append("   - Add None checks before accessing optional values")
            lines.append("   - Use optional chaining or default values")
            lines.append("   - Consider using `if value is not None:` guards")
            lines.append("")
        
        lines.append("### Priority 3: Collection Type Improvements")
        lines.append("")
        
        list_dict_count = len(all_categories.get("List/Dict", []))
        if list_dict_count > 0:
            lines.append(f"5. **Improve List/Dict Typing ({list_dict_count} issues)**")
            lines.append("   - Add proper generic type parameters")
            lines.append("   - Replace `list` with `list[T]` and `dict` with `dict[K, V]`")
            lines.append("   - Use more specific collection types when appropriate")
            lines.append("")
        
        lines.append("### Priority 4: Async Type Correctness")
        lines.append("")
        
        async_count = len(all_categories.get("Async", []))
        if async_count > 0:
            lines.append(f"6. **Fix Async Typing Issues ({async_count} issues)**")
            lines.append("   - Ensure async functions are properly awaited")
            lines.append("   - Add correct return type annotations for async functions")
            lines.append("   - Use `Awaitable`, `Coroutine`, or `AsyncIterator` types correctly")
            lines.append("")
        
        lines.append("### Priority 5: Type Annotations")
        lines.append("")
        
        annotation_count = len(all_categories.get("Type Annotation", []))
        if annotation_count > 0:
            lines.append(f"7. **Add Missing Type Annotations ({annotation_count} issues)**")
            lines.append("   - Add type hints to function parameters")
            lines.append("   - Add return type annotations")
            lines.append("   - Add type hints to variables where inference fails")
            lines.append("")
        
        # Detailed file-level hotspots
        lines.append("## 🔥 Hotspot Files (Most Issues)")
        lines.append("")
        
        file_issue_counts = defaultdict(lambda: defaultdict(int))
        for report in self.reports:
            for category, issues in report.issues_by_category.items():
                for issue in issues:
                    file_key = f"{report.repo_name}::{issue.file}"
                    file_issue_counts[file_key]["total"] += 1
                    file_issue_counts[file_key][category] += 1
        
        sorted_files = sorted(
            file_issue_counts.items(),
            key=lambda x: x[1]["total"],
            reverse=True
        )[:20]
        
        for file_key, counts in sorted_files:
            repo_name, file_path = file_key.split("::", 1)
            lines.append(f"### {repo_name}: `{file_path}` ({counts['total']} issues)")
            lines.append("")
            
            # Show category breakdown
            category_list = [(cat, count) for cat, count in counts.items() if cat != "total"]
            category_list.sort(key=lambda x: x[1], reverse=True)
            
            for category, count in category_list:
                lines.append(f"- {category}: {count}")
            
            lines.append("")
        
        # Next steps
        lines.append("## 📋 Recommended Next Steps")
        lines.append("")
        lines.append("1. Review individual repository reports for detailed issue listings")
        lines.append("2. Start with Priority 1 issues (imports and return types)")
        lines.append("3. Focus on hotspot files with the most issues")
        lines.append("4. Consider running pyright incrementally after each fix")
        lines.append("5. Update pyrightconfig.json strictness levels as issues are resolved")
        lines.append("")
        
        lines.append("## 📊 Report Files")
        lines.append("")
        for report in self.reports:
            lines.append(f"- [{report.repo_name} Detailed Report](type_check_report_{report.repo_name}.md)")
        lines.append("")
        
        return "\n".join(lines)
    
    def save_reports(self, output_dir: Path):
        """Save all reports to files."""
        output_dir.mkdir(exist_ok=True, parents=True)
        
        print("\n" + "="*80)
        print("SAVING REPORTS")
        print("="*80)
        
        # Save individual reports
        for report in self.reports:
            filename = output_dir / f"type_check_report_{report.repo_name}.md"
            content = self.generate_individual_report(report)
            filename.write_text(content, encoding="utf-8")
            print(f"Saved: {filename}")
        
        # Save consolidated summary
        summary_filename = output_dir / "type_check_summary_consolidated.md"
        summary_content = self.generate_consolidated_summary()
        summary_filename.write_text(summary_content, encoding="utf-8")
        print(f"Saved: {summary_filename}")
        
        print("\n✅ All reports generated successfully!")
        print(f"📁 Reports location: {output_dir.absolute()}")
    
    def run(self):
        """Run type checking on all repositories."""
        print("="*80)
        print("TYPE CHECK ORCHESTRATOR")
        print("="*80)
        print(f"Root directory: {self.root_dir}")
        print(f"Timestamp: {self.timestamp}")
        print("")
        
        # Check all repositories
        self.check_plexichat()
        self.check_common_utils()
        self.check_encryption()
        
        # Generate and save reports
        output_dir = self.root_dir / "type_check_reports"
        self.save_reports(output_dir)
        
        # Print summary to console
        print("\n" + "="*80)
        print("QUICK SUMMARY")
        print("="*80)
        
        for report in self.reports:
            status = "✅" if report.success else "❌"
            print(f"{status} {report.repo_name}: {report.total_errors} errors, {report.total_warnings} warnings")


def main():
    """Main entry point."""
    root_dir = Path(__file__).parent.parent
    orchestrator = TypeCheckOrchestrator(root_dir)
    orchestrator.run()


if __name__ == "__main__":
    main()
