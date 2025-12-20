# Type Checking System for PlexiChat

Comprehensive type checking infrastructure for PlexiChat and all related repositories.

## 🎯 Overview

This system runs **pyright** type checking across three repositories simultaneously:

1. **plexichat** - Main application
2. **common-utils** - Shared utilities (submodule)
3. **encryption** - Encryption utilities

All three repositories use the same `pyrightconfig.json` configuration for consistent type checking rules.

## 🚀 Quick Start

### Prerequisites

```bash
# Install pyright
npm install -g pyright

# Setup Python environment
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt

# Initialize submodules
git submodule update --init --recursive
```

### Run Type Checking

**Option 1: Convenience Scripts (Recommended)**

```bash
# Windows
.\scripts\run_type_check.ps1

# Linux/Mac
chmod +x scripts/run_type_check.sh
./scripts/run_type_check.sh
```

**Option 2: Direct Python**

```bash
# Human-readable markdown reports
python scripts/type_check_all.py

# JSON output for automation
python scripts/type_check_json.py > results.json
```

## 📊 Generated Reports

All reports are saved to `type_check_reports/` directory:

### 1. Consolidated Summary (Start Here!)
**File:** `type_check_summary_consolidated.md`

This is your primary report containing:
- Overall statistics across all repositories
- Issues categorized by type (Optional, Return Type, Import, etc.)
- **Prioritized fix recommendations** (most important!)
- Hotspot files with the most issues
- Repository breakdown
- Next steps guidance

### 2. Individual Repository Reports
**Files:** 
- `type_check_report_plexichat.md`
- `type_check_report_common-utils.md`
- `type_check_report_encryption.md`

Detailed breakdowns per repository with:
- File-level issue listings
- Issues grouped by category
- Top problematic files

## 📋 Issue Categories

Issues are automatically categorized for easier prioritization:

| Priority | Category | Description |
|----------|----------|-------------|
| 🔴 1 | **Import** | Missing/incorrect imports - blocks other checks |
| 🔴 1 | **Return Type** | Function return type mismatches - breaks contracts |
| 🔴 1 | **Argument Type** | Function argument mismatches - causes runtime errors |
| 🟡 2 | **Optional** | Missing None checks - potential AttributeErrors |
| 🟡 2 | **List/Dict** | Collection type annotations needed |
| 🟡 2 | **Async** | Async/await typing issues |
| 🟢 3 | **Type Annotation** | Missing type hints |
| 🟢 3 | **Assignment** | Variable assignment type issues |
| ⚪ 4 | **Other** | Uncategorized issues |

## 🎯 Fixing Issues by Priority

### Priority 1: Critical Issues (Fix First!)

**Import Errors:**
```python
# ❌ Bad
from missing_module import something

# ✅ Good
from src.core.auth import AuthManager
```

**Return Type Mismatches:**
```python
# ❌ Bad
def get_user(user_id: int) -> User:
    if not user_id:
        return None  # Error!

# ✅ Good
def get_user(user_id: int) -> User | None:
    if not user_id:
        return None
    return User(id=user_id)
```

### Priority 2: Optional Handling

```python
# ❌ Bad
def get_name(user: User | None) -> str:
    return user.name  # Error if None!

# ✅ Good
def get_name(user: User | None) -> str:
    if user is None:
        return "Anonymous"
    return user.name
```

### Priority 3: Collection Types

```python
# ❌ Bad
def process(items: list) -> dict:
    return {item.id: item.name for item in items}

# ✅ Good
def process(items: list[Item]) -> dict[int, str]:
    return {item.id: item.name for item in items}
```

See [TYPE_CHECK_GUIDE.md](TYPE_CHECK_GUIDE.md) for comprehensive fixing examples.

## 🔧 Configuration

### pyrightconfig.json

The main configuration file sets type checking rules:

```json
{
  "include": ["src"],
  "exclude": ["src/tests", "src/utils/common-utils"],
  "pythonVersion": "3.13",
  "typeCheckingMode": "standard",
  "reportMissingImports": "error",
  "reportOptionalMemberAccess": "error",
  "reportOptionalSubscript": "error",
  "reportArgumentType": "error",
  "reportReturnType": "error"
}
```

Increase strictness gradually as you fix issues:
- `"typeCheckingMode": "strict"` - More comprehensive checks
- Add more `report*: "error"` rules for stricter enforcement

## 🤖 Programmatic Usage

### JSON Output

```bash
python scripts/type_check_json.py > results.json
```

The JSON format is documented in `scripts/report_schema.json` and includes:
- Metadata (timestamp, config used)
- Per-repository results with categorized issues
- Aggregated statistics across all repos
- Hotspot file listings

### Example: CI/CD Integration

```python
import subprocess, json, sys

result = subprocess.run(
    ["python", "scripts/type_check_json.py"],
    capture_output=True, text=True
)
report = json.loads(result.stdout)

errors = report["aggregated_statistics"]["total_errors"]
if errors > 0:
    print(f"❌ Type check failed: {errors} errors")
    sys.exit(1)
```

See `scripts/example_json_consumer.py` for complete examples including:
- Quality metrics calculation
- Threshold checking for CI/CD
- Filtering by category or file pattern
- Dashboard integration

## 🔄 CI/CD Integration

Pre-built configurations for popular platforms:

### GitLab CI
```yaml
# .gitlab-ci.yml
include:
  - local: scripts/gitlab-ci-typecheck.yml
```

### GitHub Actions
```yaml
# .github/workflows/typecheck.yml
# Copy from scripts/github-actions-typecheck.yml
```

Both provide:
- ✅ Type checking on every PR
- ✅ Strict mode for main branch (blocks on errors)
- ✅ Weekly scheduled reports
- ✅ Artifact uploads
- ✅ PR comments with results

See `scripts/gitlab-ci-typecheck.yml` and `scripts/github-actions-typecheck.yml` for full examples.

## 📈 Development Workflow

### Before Committing
```bash
# Quick check on files you modified
pyright src/core/messaging/manager.py
```

### Before Creating PR
```bash
# Full type check with reports
python scripts/type_check_all.py

# Review consolidated summary
less type_check_reports/type_check_summary_consolidated.md
```

### Weekly Quality Review
```bash
# Generate fresh reports
python scripts/type_check_all.py

# Track progress over time
git diff HEAD~7 type_check_reports/type_check_summary_consolidated.md
```

### Incremental Fixing Strategy

1. **Initial Assessment**
   ```bash
   python scripts/type_check_all.py
   ```
   Review `type_check_summary_consolidated.md` for overview

2. **Fix by Priority**
   - Start with Priority 1 (Import, Return Type, Argument Type)
   - Focus on "Hotspot Files" section
   - Fix one category at a time

3. **Verify Progress**
   ```bash
   # Check specific file
   pyright src/api/routes/auth.py
   
   # Full recheck
   python scripts/type_check_all.py
   ```

4. **Repeat** until critical issues resolved

## 📚 Documentation

- **[TYPE_CHECK_GUIDE.md](TYPE_CHECK_GUIDE.md)** - Complete guide with examples
- **[scripts/README.md](scripts/README.md)** - Technical details on scripts
- **[scripts/report_schema.json](scripts/report_schema.json)** - JSON output schema
- **[AGENTS.md](AGENTS.md)** - Development setup and conventions

## 🛠️ Troubleshooting

### "pyright not found"
```bash
npm install -g pyright
pyright --version
```

### "common-utils does not exist"
```bash
git submodule update --init --recursive
ls src/utils/common-utils
```

### Too many errors to start
```bash
# Check one module at a time
pyright src/core/auth/
pyright src/api/routes/
```

### Need to suppress specific error
```python
# Use sparingly - only for legitimate cases
result = some_function()  # type: ignore[return-value]
```

## 📊 Report Artifacts

The system generates several useful artifacts:

```
type_check_reports/
├── type_check_summary_consolidated.md  # Main summary - read this first!
├── type_check_report_plexichat.md      # Detailed: plexichat repo
├── type_check_report_common-utils.md   # Detailed: common-utils repo
└── type_check_report_encryption.md     # Detailed: encryption repo
```

These files are:
- ✅ Human-readable markdown
- ✅ Version control friendly (text diff)
- ✅ GitHub/GitLab compatible
- ✅ Automatically ignored by git (see `.gitignore`)

## 🎨 Customization

### Add New Category

Edit `scripts/type_check_all.py`:

```python
CATEGORY_PATTERNS = {
    # ... existing categories ...
    "Your Category": [
        "pattern1",
        "pattern2",
    ],
}
```

### Adjust Strictness

Edit `pyrightconfig.json`:

```json
{
  "typeCheckingMode": "strict",  // More strict
  "reportUnknownParameterType": "error",  // Require param types
  // ... add more rules ...
}
```

### Custom Thresholds

Edit `scripts/example_json_consumer.py`:

```python
def check_thresholds(report, max_errors=0, max_warnings=10):
    # Adjust thresholds to your standards
    ...
```

## 🎯 Goals & Best Practices

1. **Start Early** - Type new code immediately
2. **Fix Imports First** - They block other type checks
3. **Use Reports** - Don't guess, use the consolidated summary
4. **Incremental Progress** - Fix one category or file at a time
5. **Run Frequently** - Catch issues early
6. **Team Review** - Use reports in code review discussions
7. **CI Integration** - Prevent new type errors automatically

## 📞 Support

For questions or issues:
1. Check this README and related documentation
2. Review the consolidated summary report for guidance
3. See [TYPE_CHECK_GUIDE.md](TYPE_CHECK_GUIDE.md) for detailed examples
4. Check pyright documentation for specific error codes

## 📝 Summary

This type checking system provides:

✅ **Comprehensive** - Checks all three repositories simultaneously  
✅ **Organized** - Issues categorized and prioritized automatically  
✅ **Actionable** - Clear recommendations on what to fix first  
✅ **Automated** - Easy CI/CD integration for continuous quality  
✅ **Flexible** - Both human-readable and machine-readable outputs  
✅ **Progressive** - Incremental improvement strategy built-in  

Start with `python scripts/type_check_all.py` and review the consolidated summary!
