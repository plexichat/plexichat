# Test Verification - Quick Reference

## Installation

```bash
pip install -r requirements-test.txt
```

## Running Tests

### Quick Commands

```bash
# All tests (recommended)
pytest -n auto -m "not slow"

# Fast unit tests
pytest -m unit

# Security tests
pytest -m security -v

# With coverage
pytest --cov=src --cov-report=html
```

### Using Makefile

```bash
make test              # All tests
make test-fast         # Unit tests only
make test-security     # Security tests
make test-coverage     # With HTML coverage
make test-parallel     # Parallel execution
make test-ci           # CI/CD verification
```

### Using Test Runners

```bash
# Python runner
python test_runner.py --fast
python test_runner.py --security
python test_runner.py --module auth --coverage

# PowerShell runner (Windows)
.\run_tests.ps1 -Fast
.\run_tests.ps1 -Security
.\run_tests.ps1 -Module auth -Coverage
```

## Verification

```bash
# Complete CI/CD verification
python ci_test_verification.py

# Generate dashboard
python test_dashboard.py
python test_dashboard.py --html
```

## Coverage Targets

| Repository | Target | Command |
|-----------|--------|---------|
| PlexiChat | 85%+ | `pytest --cov=src --cov-fail-under=85` |
| Common Utils | 90%+ | `pytest src/utils/common-utils --cov-fail-under=90` |
| Client | 80%+ | Check client test framework |

## Test Markers

```bash
pytest -m unit          # Fast unit tests
pytest -m integration   # Full module tests
pytest -m security      # Security tests (CRITICAL)
pytest -m slow          # Time-dependent tests
pytest -m auth          # Auth module
pytest -m messaging     # Messaging module
pytest -m api           # API tests
```

## Common Issues

### Tests Too Slow
```bash
# Run in parallel
pytest -n auto

# Identify slow tests
pytest --durations=20
```

### Low Coverage
```bash
# See missing lines
pytest --cov=src --cov-report=term-missing

# Generate HTML report
pytest --cov=src --cov-report=html
# Open htmlcov/index.html
```

### Database Issues
```bash
# Clean test database
rm -rf temp/test_session/
pytest
```

## Reports Location

- **Test Results**: `test-results.xml`
- **Coverage**: `coverage.xml`, `htmlcov/`
- **Performance**: `test-reports/performance.txt`
- **Security**: `test-reports/security.txt`
- **Metrics**: `test-reports/metrics.txt`
- **Dashboard**: `test-dashboard.html` (after `python test_dashboard.py --html`)

## Writing Tests

```python
import pytest

@pytest.mark.auth
def test_login(modules, user_factory):
    """Test user login."""
    user = user_factory.create()
    result = modules.auth.login(user.username, "TestPass123!")
    assert result.success

@pytest.mark.security
def test_sql_injection(modules):
    """Test SQL injection prevention."""
    with pytest.raises(SecurityException):
        modules.db.execute("'; DROP TABLE users; --")
```

## CI/CD

Tests run automatically in GitLab CI:
1. **Lint** (5-10 min) - Code quality
2. **Security** (5-10 min) - Secret detection
3. **Test** (20-30 min) - Full suite + coverage

Total: ~30-40 minutes

## Support

- Full docs: `docs/TESTING.md`
- Test suite: `src/tests/README.md`
- Summary: `TEST_VERIFICATION_SUMMARY.md`
