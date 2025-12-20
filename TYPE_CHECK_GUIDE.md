# Type Checking Guide for PlexiChat

This guide explains how to run comprehensive type checking across all PlexiChat repositories and interpret the results.

## Quick Start

### Prerequisites

1. **Install pyright:**
   ```bash
   npm install -g pyright
   ```

2. **Initialize submodules:**
   ```bash
   git submodule update --init --recursive
   ```

3. **Activate virtual environment:**
   ```bash
   # Windows
   .venv\Scripts\activate
   
   # Linux/Mac
   source .venv/bin/activate
   ```

### Running Type Checks

**Windows (PowerShell):**
```powershell
.\scripts\run_type_check.ps1
```

**Linux/Mac:**
```bash
chmod +x scripts/run_type_check.sh
./scripts/run_type_check.sh
```

**Direct Python:**
```bash
python scripts/type_check_all.py
```

## What Gets Checked

The type checker runs pyright on three repositories:

1. **plexichat** - Main application code
2. **common-utils** - Shared utilities submodule
3. **encryption** - Encryption utilities module

All three use the same `pyrightconfig.json` for consistent type checking rules.

## Understanding the Reports

### Report Files

After running, check `type_check_reports/`:

- **`type_check_summary_consolidated.md`** - Start here! Cross-repository summary with prioritized recommendations
- **`type_check_report_plexichat.md`** - Detailed issues for main repository
- **`type_check_report_common-utils.md`** - Detailed issues for common-utils submodule
- **`type_check_report_encryption.md`** - Detailed issues for encryption module

### Issue Categories

Issues are automatically categorized:

| Category | Description | Priority |
|----------|-------------|----------|
| **Import** | Missing or incorrect imports | 🔴 Critical |
| **Return Type** | Function return type mismatches | 🔴 Critical |
| **Argument Type** | Function argument type mismatches | 🔴 Critical |
| **Optional** | Optional type access without None checks | 🟡 High |
| **List/Dict** | Collection type annotation issues | 🟡 High |
| **Async** | Async/await typing issues | 🟡 High |
| **Type Annotation** | Missing or incomplete type hints | 🟢 Medium |
| **Assignment** | Variable assignment type issues | 🟢 Medium |
| **Other** | Uncategorized issues | 🟢 Medium |

## Fixing Issues

### Priority 1: Critical Type Safety (Fix First)

#### Import Errors
```python
# ❌ Bad
from missing_module import something

# ✅ Good
from src.core.auth import AuthManager
```

#### Return Type Mismatches
```python
# ❌ Bad
def get_user(user_id: int) -> User:
    if not user_id:
        return None  # Error: None is not User

# ✅ Good
def get_user(user_id: int) -> User | None:
    if not user_id:
        return None
    return User(id=user_id)
```

#### Argument Type Mismatches
```python
# ❌ Bad
def process_data(data: dict[str, int]) -> None:
    pass

process_data({"key": "value"})  # Error: str is not int

# ✅ Good
def process_data(data: dict[str, int]) -> None:
    pass

process_data({"key": 123})
```

### Priority 2: Optional Handling

```python
# ❌ Bad
def get_username(user: User | None) -> str:
    return user.username  # Error: user could be None

# ✅ Good - Option 1: Guard clause
def get_username(user: User | None) -> str:
    if user is None:
        return "Anonymous"
    return user.username

# ✅ Good - Option 2: Default value
def get_username(user: User | None) -> str:
    return user.username if user else "Anonymous"
```

### Priority 3: Collection Type Improvements

```python
# ❌ Bad
def process_items(items: list) -> dict:
    result = {}
    for item in items:
        result[item.id] = item.name
    return result

# ✅ Good
def process_items(items: list[Item]) -> dict[int, str]:
    result: dict[int, str] = {}
    for item in items:
        result[item.id] = item.name
    return result
```

### Priority 4: Async Type Correctness

```python
# ❌ Bad
async def fetch_data() -> Data:
    data = get_from_db()  # Missing await
    return data

# ✅ Good
async def fetch_data() -> Data:
    data = await get_from_db()
    return data

# ✅ Also good - Explicit return type
from typing import Coroutine

async def fetch_data() -> Data:
    data = await get_from_db()
    return data
```

### Priority 5: Type Annotations

```python
# ❌ Bad
def calculate(a, b):
    return a + b

# ✅ Good
def calculate(a: int, b: int) -> int:
    return a + b

# ✅ Also good - More specific
def calculate(a: int | float, b: int | float) -> int | float:
    return a + b
```

## Incremental Fixing Strategy

### Step 1: Run Initial Check
```bash
python scripts/type_check_all.py
```

### Step 2: Review Consolidated Summary
Open `type_check_reports/type_check_summary_consolidated.md` and:
1. Check overall statistics
2. Review prioritized recommendations
3. Identify hotspot files

### Step 3: Fix by Priority
1. Start with Priority 1 issues (imports, return types, arguments)
2. Focus on files in the "Hotspot Files" section
3. Fix one category at a time

### Step 4: Verify Fixes
After fixing a category or file:
```bash
# Quick check on specific file
pyright src/api/routes/auth.py

# Full check on all repos
python scripts/type_check_all.py
```

### Step 5: Iterate
Repeat steps 2-4 until all critical issues are resolved.

## Integration with Development

### Before Committing
```bash
# Check only files you modified
pyright src/core/messaging/manager.py src/core/messaging/models.py
```

### Before Merging PR
```bash
# Full type check
python scripts/type_check_all.py

# Review only your repository
less type_check_reports/type_check_report_plexichat.md
```

### Weekly Quality Review
```bash
# Generate fresh reports
python scripts/type_check_all.py

# Track progress over time
git diff HEAD~7 type_check_reports/type_check_summary_consolidated.md
```

## Configuration

### pyrightconfig.json
The main configuration file controls type checking strictness:

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

### Adjusting Strictness

As you fix issues, you can increase strictness:

```json
{
  "typeCheckingMode": "strict",  // More strict
  "reportUnknownParameterType": "error",  // Require all param types
  "reportUnknownVariableType": "error",   // Require all variable types
  "reportUnknownMemberType": "error"      // Require all member types
}
```

## Troubleshooting

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

### Too Many Errors
Start with specific files or directories:
```bash
pyright src/core/auth/
pyright src/api/routes/
```

### False Positives
Use type ignore comments sparingly:
```python
# For legitimate cases only
result = some_function()  # type: ignore[return-value]
```

## Best Practices

1. **Add types as you go** - Type new code immediately
2. **Fix imports first** - They block other checks
3. **Use strict mode gradually** - Enable stricter rules incrementally
4. **Document complex types** - Add comments for non-obvious type choices
5. **Run checks frequently** - Catch issues early
6. **Review reports together** - Use consolidated summary for team review

## Resources

- [Pyright Documentation](https://github.com/microsoft/pyright)
- [Python Type Hints PEP 484](https://peps.python.org/pep-0484/)
- [Python Type Checking Guide](https://mypy.readthedocs.io/en/stable/)
- [PlexiChat AGENTS.md](AGENTS.md) - Development setup and conventions

## Support

For issues or questions about type checking:
1. Check this guide and consolidated summary
2. Review individual repository reports for details
3. Check pyright documentation for specific error codes
4. Consult team members for complex type issues
