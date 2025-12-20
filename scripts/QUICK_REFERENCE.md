# Type Check Quick Reference Card

## 🚀 Run Type Checks

```bash
# Quick - Human readable reports
python scripts/type_check_all.py

# Quick - JSON for automation
python scripts/type_check_json.py > results.json

# Wrapper scripts (recommended)
.\scripts\run_type_check.ps1         # Windows
./scripts/run_type_check.sh          # Linux/Mac
```

## 📊 View Results

```bash
# Main summary (read this first!)
less type_check_reports/type_check_summary_consolidated.md

# Individual repo details
less type_check_reports/type_check_report_plexichat.md
less type_check_reports/type_check_report_common-utils.md
less type_check_reports/type_check_report_encryption.md
```

## 🎯 Priority Fix Order

1. 🔴 **Import errors** - Fix first, blocks other checks
2. 🔴 **Return type mismatches** - Breaks function contracts
3. 🔴 **Argument type mismatches** - Causes runtime errors
4. 🟡 **Optional handling** - Add None checks
5. 🟡 **Collection types** - Add `list[T]`, `dict[K,V]`
6. 🟡 **Async typing** - Fix await/async issues
7. 🟢 **Type annotations** - Add missing hints

## 🔧 Quick Fixes

### Import Error
```python
# ❌ from missing import X
# ✅ from src.core.auth import AuthManager
```

### Return Type
```python
# ❌ def f() -> User: return None
# ✅ def f() -> User | None: return None
```

### Optional
```python
# ❌ def f(x: X | None): return x.attr
# ✅ def f(x: X | None): return x.attr if x else default
```

### Argument Type
```python
# ❌ def f(x: int): pass; f("string")
# ✅ def f(x: int): pass; f(123)
```

### Collection Type
```python
# ❌ def f() -> list: return []
# ✅ def f() -> list[int]: return []
```

## 🤖 CI/CD Integration

### GitLab CI
```yaml
include:
  - local: scripts/gitlab-ci-typecheck.yml
```

### GitHub Actions
Copy `scripts/github-actions-typecheck.yml` to `.github/workflows/`

### Custom CI
```bash
python scripts/type_check_json.py > results.json
# Parse results.json for errors/warnings
# Exit 1 if errors > threshold
```

## 🔍 Single File/Directory Check

```bash
# Check specific file
pyright src/api/routes/auth.py

# Check specific directory
pyright src/core/auth/

# Check without config
pyright --project pyrightconfig.json src/
```

## 📈 Track Progress

```bash
# Compare with last week
git diff HEAD~7 type_check_reports/type_check_summary_consolidated.md

# Count current errors
python scripts/type_check_json.py | python -c "import json,sys; print(json.load(sys.stdin)['aggregated_statistics']['total_errors'])"
```

## 🛠️ Configuration

### Adjust Strictness
Edit `pyrightconfig.json`:
```json
{
  "typeCheckingMode": "strict",  // or "standard", "basic"
  "reportOptionalMemberAccess": "error",  // or "warning", "none"
}
```

### Ignore Specific Error
```python
result = func()  # type: ignore[return-value]
```

## 📚 Full Documentation

- **[TYPE_CHECK_README.md](../TYPE_CHECK_README.md)** - Overview
- **[TYPE_CHECK_GUIDE.md](../TYPE_CHECK_GUIDE.md)** - Complete guide
- **[scripts/README.md](README.md)** - Script details
- **[AGENTS.md](../AGENTS.md)** - Dev conventions

## ⚡ Troubleshooting

```bash
# Install pyright
npm install -g pyright

# Update submodules
git submodule update --init --recursive

# Check pyright version
pyright --version

# Test on small file first
pyright src/utils/__init__.py
```

## 💡 Tips

- Start with **hotspot files** from consolidated summary
- Fix one **category** at a time
- Run checks **before committing**
- Review **Priority 1** issues first
- Use **consolidated summary** as roadmap
- **Don't** add `# type: ignore` everywhere - fix the root cause

## 🎯 Workflow

1. `python scripts/type_check_all.py`
2. Open `type_check_summary_consolidated.md`
3. Check "Hotspot Files" section
4. Fix Priority 1 issues in top files
5. Run `pyright <file>` to verify
6. Repeat!
