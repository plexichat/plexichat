# Type Checking Scripts

This directory contains scripts for running comprehensive type checking across all PlexiChat repositories.

**📌 Quick Start:** See [QUICK_REFERENCE.md](QUICK_REFERENCE.md) for a one-page command reference.

**📚 Full Guide:** See [TYPE_CHECK_README.md](../TYPE_CHECK_README.md) for complete documentation.

## Type Check All Repositories

This directory contains two type checking scripts:

### Human-Readable Reports: `type_check_all.py`

Generates detailed markdown reports with categorized issues and recommendations.

Checks:
1. **plexichat** - Main repository
2. **common-utils** - Submodule at `src/utils/common-utils`
3. **encryption** - Module at `src/utils/encryption`

### JSON Output: `type_check_json.py`

Outputs structured JSON for programmatic consumption (CI/CD, dashboards, etc.).
Uses the schema defined in `report_schema.json`

### Prerequisites

1. Install pyright globally:
   ```bash
   npm install -g pyright
   ```

2. Activate the virtual environment and install dependencies:
   ```bash
   .venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```

3. Ensure the common-utils submodule is initialized:
   ```bash
   git submodule update --init --recursive
   ```

### Usage

**Markdown Reports (recommended for humans):**
```bash
python scripts/type_check_all.py
# Or use the convenience wrapper:
# Windows: .\scripts\run_type_check.ps1
# Linux/Mac: ./scripts/run_type_check.sh
```

**JSON Output (recommended for automation):**
```bash
python scripts/type_check_json.py > type_check_results.json
```

### Output

The script generates detailed reports in the `type_check_reports/` directory:

1. **Individual Repository Reports:**
   - `type_check_report_plexichat.md` - Detailed issues for main repo
   - `type_check_report_common-utils.md` - Detailed issues for common-utils
   - `type_check_report_encryption.md` - Detailed issues for encryption

2. **Consolidated Summary:**
   - `type_check_summary_consolidated.md` - Cross-repository analysis with:
     - Overall statistics
     - Issues organized by category (Optional, Return Type, Import, etc.)
     - Prioritized fix recommendations
     - Hotspot files with the most issues
     - Next steps guidance

### Report Categories

Issues are automatically categorized as:

- **Optional** - Optional type access without None checks
- **List/Dict** - Collection type annotation issues
- **Return Type** - Function return type mismatches
- **Import** - Missing or incorrect imports
- **Async** - Async/await typing issues
- **Type Annotation** - Missing or incomplete type hints
- **Argument Type** - Function argument type mismatches
- **Assignment** - Variable assignment type issues
- **Other** - Uncategorized issues

### Configuration

The script uses the `pyrightconfig.json` from the repository root for all three repositories. This ensures consistent type checking rules across all code.

### Example Output

```
================================================================================
TYPE CHECK ORCHESTRATOR
================================================================================
Root directory: /path/to/plexichat
Timestamp: 2025-01-20_14-30-00

================================================================================
CHECKING: PlexiChat (main repository)
================================================================================
Running: pyright --outputjson --project /path/to/pyrightconfig.json in /path/to/plexichat
Errors: 42, Warnings: 3, Info: 0

================================================================================
CHECKING: common-utils (submodule)
================================================================================
Running: pyright --outputjson --project /path/to/pyrightconfig.json in /path/to/common-utils
Errors: 15, Warnings: 1, Info: 0

================================================================================
CHECKING: encryption (module)
================================================================================
Running: pyright --outputjson --project /path/to/pyrightconfig.json in /path/to/encryption
Errors: 8, Warnings: 0, Info: 0

================================================================================
SAVING REPORTS
================================================================================
Saved: type_check_reports/type_check_report_plexichat.md
Saved: type_check_reports/type_check_report_common-utils.md
Saved: type_check_reports/type_check_report_encryption.md
Saved: type_check_reports/type_check_summary_consolidated.md

✅ All reports generated successfully!
📁 Reports location: /path/to/plexichat/type_check_reports

================================================================================
QUICK SUMMARY
================================================================================
✅ plexichat: 42 errors, 3 warnings
✅ common-utils: 15 errors, 1 warning
✅ encryption: 8 errors, 0 warnings
```

### Using JSON Output Programmatically

The `example_json_consumer.py` script demonstrates how to consume JSON output:

```bash
python scripts/example_json_consumer.py
```

**Example use cases:**

1. **CI/CD Integration:**
```python
# Fail build if errors exceed threshold
import subprocess, json, sys

result = subprocess.run(
    ["python", "scripts/type_check_json.py"],
    capture_output=True, text=True
)
report = json.loads(result.stdout)

if report["aggregated_statistics"]["total_errors"] > 0:
    print("❌ Type check failed")
    sys.exit(1)
```

2. **Dashboard/Metrics:**
```python
# Extract metrics for tracking
report = json.loads(subprocess.run(...).stdout)
stats = report["aggregated_statistics"]

print(f"Type Safety Score: {stats['total_errors']}")
print(f"Hottest File: {stats['hotspot_files'][0]['file']}")
```

3. **Filter by Category:**
```python
# Get all Optional issues
optional_issues = [
    issue
    for repo in report["repositories"]
    for issues in repo["categories"].get("Optional", [])
    for issue in issues
]
```

See `example_json_consumer.py` for more examples.

### Troubleshooting

**Error: "pyright not found"**
- Install pyright globally: `npm install -g pyright`
- Verify installation: `pyright --version`

**Error: "common-utils does not exist"**
- Initialize submodules: `git submodule update --init --recursive`

**Error: "Cannot parse JSON output"**
- Check that pyright is up to date: `npm update -g pyright`
- Ensure you're running from the repository root

### CI/CD Integration

Example configurations are provided for popular CI/CD platforms:

- **GitLab CI:** See `gitlab-ci-typecheck.yml`
- **GitHub Actions:** See `github-actions-typecheck.yml`

Both include examples for:
- Running type checks on every PR
- Blocking merges on type errors (strict mode)
- Weekly scheduled reports
- Uploading artifacts
- PR comments with results

### Integration with Development Workflow

Consider running this script:
- Before starting major refactoring
- After making significant type annotation changes
- As part of a weekly code quality review
- Before merging feature branches

The prioritized recommendations help focus efforts on the most impactful type safety improvements.
