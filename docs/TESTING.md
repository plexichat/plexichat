# Comprehensive Testing Guide

This document describes the comprehensive test verification system for PlexiChat.

## Overview

The test suite consists of **3000+ tests** across multiple repositories:
- **PlexiChat Main**: 2000+ tests (target: 85% coverage)
- **Common Utils**: 500+ tests (target: 90% coverage)
- **Client**: 500+ tests (target: 80% coverage)

All tests must complete in **<30 minutes** and **no security violations** are allowed.

## Quick Start

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests (fast)
pytest src/tests/ -n auto -m "not slow"

# Run with coverage
pytest src/tests/ -n auto --cov=src --cov-report=html

# Run specific module
pytest src/tests/auth/ -v

# Run security tests only
pytest -m security -v
```

## Test Runners

### Python Runner (Cross-platform)

```bash
# Run all tests
python run_tests.py

# Run with options
python run_tests.py --verbose --parallel --coverage

# Run specific tests
python test_runner.py --fast              # Unit tests only
python test_runner.py --security          # Security tests only
python test_runner.py --module auth       # Specific module
python test_runner.py --coverage-html     # With HTML coverage report
```

### PowerShell Runner (Windows)

```powershell
# Run all tests
.\run_tests.ps1

# Run with options
.\run_tests.ps1 -Fast -Parallel -Coverage

# Run specific module
.\run_tests.ps1 -Module auth -Verbose
```

### CI/CD Verification

```bash
# Complete verification (used in CI/CD)
python ci_test_verification.py
```

This verifies:
- ✓ All tests pass
- ✓ Test count ≥ 3000
- ✓ Coverage targets met
- ✓ Duration < 30 minutes
- ✓ No security violations

## Test Organization

### Directory Structure

```
src/tests/
├── conftest.py              # Session-scoped fixtures
├── pytest_plugins.py        # Custom pytest plugins
├── fixtures/                # Shared test fixtures
│   ├── config.py           # Test configuration
│   ├── database.py         # Database fixtures
│   ├── factories.py        # Data factories
│   └── modules.py          # Module fixtures
├── unit/                    # Fast unit tests
├── api/                     # API endpoint tests
├── auth/                    # Authentication tests
├── messaging/               # Messaging tests
├── servers/                 # Server tests
├── security/                # Security tests (CRITICAL)
└── ...                      # Other modules
```

### Test Markers

Tests are automatically marked based on their location:

| Marker | Description | Usage |
|--------|-------------|-------|
| `unit` | Fast unit tests | `pytest -m unit` |
| `integration` | Full module tests | `pytest -m integration` |
| `slow` | Time-dependent tests | `pytest -m slow` |
| `security` | **Security-critical tests** | `pytest -m security` |
| `auth` | Authentication module | `pytest -m auth` |
| `messaging` | Messaging module | `pytest -m messaging` |
| `servers` | Server module | `pytest -m servers` |

**Note**: Security tests are monitored separately and any failure causes the build to fail.

## Coverage Requirements

### Targets by Repository

| Repository | Target | Path |
|-----------|--------|------|
| PlexiChat Main | 85% | `src/` |
| Common Utils | 90% | `src/utils/common-utils/` |
| Client | 80% | `client/` |

### Critical Modules (Higher Targets)

- **Authentication**: 90% (security-critical)
- **Authorization**: 90% (security-critical)
- **Encryption**: 90% (security-critical)
- **API Security**: 90% (security-critical)

### Coverage Reports

```bash
# Generate HTML coverage report
pytest --cov=src --cov-report=html
# Open htmlcov/index.html

# Generate XML for CI/CD
pytest --cov=src --cov-report=xml

# Fail if below threshold
pytest --cov=src --cov-fail-under=85
```

## Performance Optimization

### Session-Scoped Database

Instead of creating a new database for each test file, we use a single session-scoped database:

```python
@pytest.fixture(scope="session")
def db_manager():
    """ONE database for all tests."""
    manager = DatabaseManager(test_dir="temp/test_session")
    manager.setup()
    yield manager
    manager.teardown()
```

### User Pool

Pre-create users with real Argon2 hashing at session start:

```python
def test_something(user_factory):
    user = user_factory.create()  # Fast! Uses pool
```

### Parallel Execution

```bash
# Auto-detect CPU cores
pytest -n auto

# Specific number of workers
pytest -n 4
```

### Test Ordering

Tests are automatically ordered for optimal parallel execution:
1. Fast unit tests first (quick feedback)
2. Integration tests
3. Slow tests (if included)

## Security Testing

### Security Test Requirements

Security tests are **MANDATORY** and must not fail:

```python
@pytest.mark.security
def test_sql_injection_prevention(modules):
    """Verify SQL injection is prevented."""
    with pytest.raises(SecurityException):
        modules.database.execute("'; DROP TABLE users; --")
```

### Security Test Categories

1. **Injection Attacks**
   - SQL injection
   - NoSQL injection
   - Command injection
   - LDAP injection

2. **Authentication & Authorization**
   - Authentication bypass
   - Broken authentication
   - Session hijacking
   - Token validation

3. **XSS & CSRF**
   - Cross-site scripting (stored/reflected)
   - Cross-site request forgery
   - Header injection

4. **Data Protection**
   - Encryption validation
   - Key management
   - Sensitive data exposure

### Running Security Tests

```bash
# Run all security tests
pytest -m security -v

# Run specific security category
pytest src/tests/security/test_sql_injection.py -v
pytest src/tests/security/test_authentication_bypass.py -v
pytest src/tests/security/test_xss_prevention.py -v
```

**CRITICAL**: If any security test fails, the CI/CD pipeline MUST fail.

## Test Reports

### Automatic Reports

The test suite generates several reports:

```
test-reports/
├── performance.txt      # Slow test identification
├── security.txt         # Security violations
├── metrics.txt          # Test metrics by module
└── test-summary.json    # JSON summary for CI/CD
```

### JUnit XML (for CI/CD)

```bash
pytest --junitxml=test-results.xml
```

### Performance Report

```bash
# Show 20 slowest tests
pytest --durations=20

# Show all test durations
pytest --durations=0
```

## Writing Tests

### Basic Test Pattern

```python
import pytest

@pytest.mark.auth
class TestAuthentication:
    
    def test_successful_login(self, modules, user_factory):
        """Test user can log in with valid credentials."""
        user = user_factory.create()
        result = modules.auth.login(user.username, "TestPass123!")
        
        assert result.success
        assert result.token is not None
    
    def test_failed_login_invalid_password(self, modules, user_factory):
        """Test login fails with invalid password."""
        user = user_factory.create()
        
        with pytest.raises(AuthenticationError):
            modules.auth.login(user.username, "WrongPassword")
```

### Parameterized Tests

```python
@pytest.mark.parametrize("invalid_input,expected_error", [
    ("", "Username cannot be empty"),
    ("ab", "Username too short"),
    ("x" * 100, "Username too long"),
    ("user@name", "Invalid characters"),
])
def test_invalid_username(invalid_input, expected_error, modules):
    """Test username validation."""
    with pytest.raises(ValidationError, match=expected_error):
        modules.auth.validate_username(invalid_input)
```

### Fixtures

```python
def test_with_user(test_user):
    """Use pre-created user from pool."""
    assert test_user.id is not None

def test_with_server(test_server):
    """Use pre-created server."""
    server, owner = test_server
    assert server.owner_id == owner.id

def test_with_authenticated_user(test_user_with_token):
    """Use authenticated user."""
    user, token = test_user_with_token
    assert token is not None
```

## CI/CD Integration

### GitLab CI

The `.gitlab-ci.yml` is configured with three stages:

1. **Lint** (fast feedback)
   - Ruff linting
   - Format checking

2. **Security** (critical checks)
   - Secret detection (MUST pass)
   - SAST scanning
   - Dependency scanning

3. **Test** (comprehensive validation)
   - Unit tests (fast)
   - Full test suite with coverage
   - Security test verification

### Pipeline Requirements

- **Lint stage**: Allow failures (warnings only)
- **Security stage**: Secret detection must pass
- **Test stage**: All tests must pass, coverage ≥ 85%

### Artifacts

The following artifacts are saved:

- `test-results.xml` - JUnit test results
- `coverage.xml` - Cobertura coverage
- `htmlcov/` - HTML coverage report
- `test-reports/` - Custom reports

## Troubleshooting

### Slow Tests

```bash
# Identify slow tests
pytest --durations=20

# Profile specific test
pytest src/tests/auth/test_login.py --durations=0
```

### Coverage Issues

```bash
# Show missing lines
pytest --cov=src --cov-report=term-missing

# Generate detailed HTML report
pytest --cov=src --cov-report=html
# Open htmlcov/index.html

# Check specific module
pytest --cov=src.core.auth --cov-report=term-missing
```

### Parallel Execution Issues

```bash
# Disable parallel execution
pytest -n 0

# Use specific number of workers
pytest -n 2

# Verbose output for debugging
pytest -n auto -v
```

### Database Issues

```bash
# Clean test database
rm -rf temp/test_session/

# Run with fresh database
pytest --setup-show
```

## Best Practices

### DO

✅ Use fixtures for test data
✅ Use parameterized tests for multiple inputs
✅ Mark security tests with `@pytest.mark.security`
✅ Write descriptive test names
✅ Test edge cases and error conditions
✅ Use user pool for performance
✅ Run tests in parallel

### DON'T

❌ Create users manually (use `user_factory`)
❌ Skip security tests
❌ Ignore coverage thresholds
❌ Write slow tests without `@pytest.mark.slow`
❌ Commit test data or secrets
❌ Use mock hashing in tests (use real Argon2)
❌ Create database per test

## Performance Targets

| Metric | Target | Current |
|--------|--------|---------|
| Total test count | 3000+ | TBD |
| Total duration | <30 min | TBD |
| Coverage (main) | 85%+ | TBD |
| Coverage (utils) | 90%+ | TBD |
| Coverage (client) | 80%+ | TBD |
| Security violations | 0 | TBD |
| Failed tests | 0 | TBD |

## Support

For issues or questions:
1. Check this documentation
2. Review test examples in `src/tests/`
3. Check CI/CD logs for detailed errors
4. Review `test-reports/` for analysis

## Version History

- **v1.0** - Initial comprehensive test verification system
  - 3000+ test support
  - Coverage enforcement
  - Security violation detection
  - Performance tracking
  - Parallel execution
