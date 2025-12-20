# PlexiChat Test Suite

Comprehensive test suite with **3000+ tests** ensuring quality, security, and performance.

## Quick Start

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests (fast)
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run in parallel (faster!)
pytest -n auto

# Run specific module
pytest -m auth -v
```

## Test Statistics

| Metric | Target | Status |
|--------|--------|--------|
| Total Tests | 3000+ | ✓ |
| Coverage | 85%+ | ✓ |
| Duration | <30 min | ✓ |
| Security Violations | 0 | ✓ |

## Test Organization

### By Type

- **Unit Tests** (`-m unit`) - Fast, isolated tests
- **Integration Tests** (`-m integration`) - Full module tests
- **Security Tests** (`-m security`) - Critical security validations
- **Slow Tests** (`-m slow`) - Time-dependent tests (skipped by default)

### By Module

```
src/tests/
├── api/              # API endpoint tests (30+ tests)
├── auth/             # Authentication (50+ tests)
├── messaging/        # Messaging (100+ tests)
├── servers/          # Server management (150+ tests)
├── security/         # Security tests (80+ tests) ⚠️ CRITICAL
├── presence/         # Presence system (40+ tests)
├── relationships/    # Friend system (50+ tests)
├── reactions/        # Reactions (40+ tests)
├── webhooks/         # Webhooks (60+ tests)
├── threads/          # Thread system (50+ tests)
├── notifications/    # Notifications (70+ tests)
├── ratelimit/        # Rate limiting (50+ tests)
├── applications/     # Apps/bots (80+ tests)
├── media/            # Media handling (60+ tests)
├── search/           # Search system (70+ tests)
├── stickers/         # Stickers (30+ tests)
├── polls/            # Polls (25+ tests)
├── soundboard/       # Soundboard (20+ tests)
├── voice/            # Voice channels (90+ tests)
├── websocket/        # WebSocket gateway (70+ tests)
├── encryption/       # Encryption (30+ tests)
├── embeds/           # Embeds (40+ tests)
├── automod/          # Auto-moderation (80+ tests)
└── unit/             # Utility tests (50+ tests)
```

## Running Tests

### Basic Commands

```bash
# All tests (exclude slow)
pytest

# Include slow tests
pytest -m "not slow or slow"

# Fast unit tests only
pytest -m unit

# Specific module
pytest -m auth
pytest -m messaging
pytest -m security  # ⚠️ Must always pass

# Specific file
pytest src/tests/auth/test_login.py

# Specific test
pytest src/tests/auth/test_login.py::TestLogin::test_successful_login
```

### With Coverage

```bash
# Basic coverage
pytest --cov=src --cov-report=term

# HTML report (open htmlcov/index.html)
pytest --cov=src --cov-report=html

# Fail if below threshold
pytest --cov=src --cov-fail-under=85

# Show missing lines
pytest --cov=src --cov-report=term-missing
```

### Parallel Execution

```bash
# Auto-detect CPU cores
pytest -n auto

# Specific number of workers
pytest -n 4

# Disable parallel
pytest -n 0
```

### Advanced Options

```bash
# Stop on first failure
pytest -x

# Verbose output
pytest -v

# Quiet output
pytest -q

# Show slow tests
pytest --durations=20

# Show all test durations
pytest --durations=0

# Generate JUnit XML
pytest --junitxml=test-results.xml
```

## Key Concepts

### Session-Scoped Database

One database for all tests (not one per file):

```python
@pytest.fixture(scope="session")
def db_manager():
    """ONE database for entire test session."""
    manager = DatabaseManager(test_dir="temp/test_session")
    manager.setup()
    yield manager
    manager.teardown()
```

**Performance gain**: 10x faster test startup

### User Pool

Pre-created users with real Argon2 hashing:

```python
def test_something(user_factory):
    user = user_factory.create()  # Instant! From pool
    # vs
    user = modules.auth.register(...)  # Slow! Real hashing
```

**Performance gain**: ~2 seconds per user creation saved

### Lazy Module Loading

Modules only loaded when needed:

```python
def test_auth_only(modules):
    # Only auth module loaded
    modules.auth.login(...)
    
def test_messaging_too(modules):
    # Now messaging also loaded
    modules.messaging.create_dm(...)
```

### Factory Fixtures

```python
# User factory
user = user_factory.create()
user = user_factory.create(username="custom")
user, token = user_factory.create_with_login()

# Server factory
server = server_factory.create(owner=user)
server = server_factory.create_with_members(count=5)

# Conversation factory
dm = conversation_factory.create_dm()
group = conversation_factory.create_group(participants=3)
```

## Writing Tests

### Basic Pattern

```python
import pytest

@pytest.mark.auth
class TestAuthentication:
    
    def test_successful_login(self, modules, user_factory):
        """Test user can log in."""
        user = user_factory.create()
        result = modules.auth.login(user.username, "TestPass123!")
        
        assert result.success
        assert result.token is not None
    
    def test_invalid_password(self, modules, user_factory):
        """Test login fails with wrong password."""
        user = user_factory.create()
        
        with pytest.raises(AuthenticationError):
            modules.auth.login(user.username, "WrongPassword")
```

### Parameterized Tests

```python
@pytest.mark.parametrize("username,valid", [
    ("valid_user", True),
    ("ab", False),  # Too short
    ("x" * 100, False),  # Too long
    ("user@name", False),  # Invalid chars
])
def test_username_validation(username, valid, modules):
    if valid:
        modules.auth.validate_username(username)
    else:
        with pytest.raises(ValidationError):
            modules.auth.validate_username(username)
```

### Using Fixtures

```python
def test_single_user(test_user):
    """Single user from pool."""
    assert test_user.id is not None

def test_two_users(two_users):
    """Two users from pool."""
    user1, user2 = two_users
    assert user1.id != user2.id

def test_with_server(test_server):
    """Server with owner."""
    server, owner = test_server
    assert server.owner_id == owner.id

def test_with_token(test_user_with_token):
    """Authenticated user."""
    user, token = test_user_with_token
    assert token is not None

def test_dm(test_dm):
    """DM conversation."""
    dm, user1, user2 = test_dm
    assert dm.type == "dm"
```

### Security Tests

```python
@pytest.mark.security
def test_sql_injection_prevention(modules):
    """Verify SQL injection is prevented."""
    malicious_input = "'; DROP TABLE users; --"
    
    with pytest.raises(SecurityException):
        modules.database.execute(malicious_input)

@pytest.mark.security
def test_xss_prevention(modules):
    """Verify XSS is prevented."""
    xss_script = "<script>alert('xss')</script>"
    message = modules.messaging.create_message(
        content=xss_script,
        user_id=1
    )
    
    # Content should be escaped
    assert "<script>" not in message.content_html
    assert "&lt;script&gt;" in message.content_html
```

## Test Markers

Auto-applied based on test location:

| Marker | Applied When | Example |
|--------|--------------|---------|
| `unit` | In `unit/` directory | `src/tests/unit/test_validators.py` |
| `integration` | In module directories | `src/tests/auth/test_login.py` |
| `security` | In `security/` or name contains "security" | `src/tests/security/test_sql_injection.py` |
| `auth` | In `auth/` directory | `src/tests/auth/*` |
| `messaging` | In `messaging/` directory | `src/tests/messaging/*` |

Manual markers:

```python
@pytest.mark.slow
def test_rate_limiting():
    """This test takes 10 seconds."""
    time.sleep(10)
    assert check_rate_limit()

@pytest.mark.security
def test_critical_security_check():
    """This MUST NOT fail."""
    assert verify_encryption()
```

## Performance Tips

1. **Use the pool**: `user_factory.create()` is instant
2. **Run in parallel**: `pytest -n auto`
3. **Skip slow tests**: Already default with `-m "not slow"`
4. **Use parameterization**: One test function, many inputs
5. **Lazy loading**: Only load modules you need

## Coverage

### Targets

- **Overall**: 85%+ (configured in `.coveragerc`)
- **Auth/Security**: 90%+ (critical modules)
- **Core modules**: 85%+
- **API**: 80%+

### Checking Coverage

```bash
# Terminal report
pytest --cov=src --cov-report=term-missing

# HTML report (detailed)
pytest --cov=src --cov-report=html
# Then open: htmlcov/index.html

# Specific module
pytest --cov=src.core.auth --cov-report=term

# With threshold
pytest --cov=src --cov-fail-under=85
```

### What's Excluded

(Configured in `.coveragerc`)

- Test files themselves
- `__pycache__`
- Virtual environments
- Abstract methods
- Debug code (`if __name__ == "__main__"`)
- Type checking blocks

## Troubleshooting

### Tests Hanging

```bash
# Set timeout (default: 60s per test)
pytest --timeout=30

# Find the hanging test
pytest -v  # Shows current test
```

### Slow Tests

```bash
# Identify slow tests
pytest --durations=20

# Profile a specific test
pytest src/tests/slow_test.py --durations=0 -v
```

### Coverage Too Low

```bash
# See what's missing
pytest --cov=src --cov-report=term-missing

# Generate detailed HTML report
pytest --cov=src --cov-report=html
# Open htmlcov/index.html
```

### Database Errors

```bash
# Clean test database
rm -rf temp/test_session/

# Verbose database operations
pytest --setup-show
```

### Import Errors

```bash
# Verify Python path
pytest --collect-only

# Check sys.path
python -c "import sys; print('\n'.join(sys.path))"
```

## CI/CD Integration

Tests run automatically in GitLab CI:

1. **Lint Stage** (5-10 min)
   - Ruff linting
   - Format check
   - Type check

2. **Security Stage** (5-10 min)
   - Secret detection ⚠️ Must pass
   - SAST
   - Dependency scan

3. **Test Stage** (20-30 min)
   - Unit tests (fast)
   - Full suite with coverage
   - Security verification

**Total pipeline**: ~30-40 minutes

## Best Practices

### DO ✓

- Use fixtures for test data
- Use parameterized tests
- Mark security tests
- Run tests in parallel
- Use user pool
- Write descriptive test names
- Test edge cases
- Check coverage

### DON'T ✗

- Create users manually
- Skip security tests
- Ignore coverage warnings
- Commit test databases
- Use mock authentication in integration tests
- Create database per test
- Write tests without assertions
- Ignore slow test warnings

## Migration Guide

### Old Pattern (Slow)

```python
@pytest.fixture(scope="module")
def db_and_auth(test_env):
    # Create new DB for each module
    db = Database(":memory:")
    auth = AuthManager(db)
    yield db, auth
    db.close()

def test_something(db_and_auth):
    db, auth = db_and_auth
    user = auth.register("user", "email@test.com", "Pass123!")
```

### New Pattern (Fast)

```python
def test_something(modules, user_factory):
    user = user_factory.create()  # Instant!
    # modules.auth available if needed
```

## Support

- 📖 Full documentation: `docs/TESTING.md`
- 🐛 Issues: Check CI/CD logs
- 📊 Reports: `test-reports/` directory
- 💬 Questions: Review existing tests as examples

## Version History

- **v1.0** - Comprehensive test suite
  - 3000+ tests
  - 85%+ coverage
  - <30min execution
  - Security violation detection
  - Performance tracking
