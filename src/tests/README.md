# Plexichat Test Documentation

Comprehensive testing guide for the Plexichat messaging platform. This document covers test architecture, patterns, fixtures, execution strategies, and contribution guidelines.

## Table of Contents

1. [Overview](#overview)
2. [Test Architecture](#test-architecture)
3. [Test Organization](#test-organization)
4. [Running Tests](#running-tests)
5. [Fixture System](#fixture-system)
6. [Security Testing](#security-testing)
7. [Test Patterns](#test-patterns)
8. [Coverage & Quality](#coverage--quality)
9. [CI Integration](#ci-integration)
10. [Contributing](#contributing)

---

## Overview

The Plexichat test suite is designed for **speed**, **reliability**, and **comprehensive coverage**. Key characteristics:

- **Session-scoped database**: One database per test session (not per test)
- **User pooling**: Pre-created users with real Argon2 hashing (~5 seconds startup, reused across all tests)
- **Lazy module loading**: Modules initialized only when needed
- **Security-first**: Comprehensive security test patterns and fixtures
- **Real dependencies**: No mocked hashing or crypto operations in tests

### Test Statistics

- **Hundreds of test files** covering all modules across the current workspace
- **Security tests**: XSS, SQL injection, CSRF, auth bypass, rate limiting
- **Integration tests**: Full module interaction testing
- **Unit tests**: Validators, utilities, property-based testing

---

## Test Architecture

### Session-Scoped Database Strategy

Instead of creating a new database for each test file, we create **ONE database per session**:

```python
@pytest.fixture(scope="session")
def db_manager():
    """Session-scoped database - created once, used by all tests"""
    manager = DatabaseManager(test_dir="temp/test_session")
    manager.setup()
    yield manager
    manager.teardown()
```

**Benefits:**
- 100x faster test execution
- Consistent test state across modules
- Realistic database interactions

### User Pool System

Pre-created users with real Argon2 hashing are reused across tests:

```python
@pytest.fixture(scope="session")
def session_users(modules):
    """Creates 20 users at session start (~5-10 seconds)"""
    users = []
    for i in range(20):
        user = modules.auth.register(
            username=f"pooluser_{i}_{uuid.uuid4().hex[:4]}",
            email=f"{username}@test.example.com",
            password=TEST_PASSWORD
        )
        users.append((user, username, password))
    return users
```

**When to use pool users:**
- Message sending tests
- Server interaction tests
- Relationship tests
- Any test that doesn't modify user credentials

**When to create fresh users:**
- Registration tests
- Password change tests
- 2FA setup tests
- Tests requiring specific user state

### Lazy Module Loading

Modules are loaded on-demand via `ModuleRegistry`:

```python
class ModuleRegistry:
    @property
    def auth(self):
        """Lazy load auth module"""
        if 'auth' not in self._cache:
            from src.core import auth
            auth.setup(self._db)
            self._cache['auth'] = auth
        return self._cache['auth']
```

**Benefits:**
- Faster test startup
- Only load what you need
- Better memory usage

---

## Test Organization

### Directory Structure

```
src/tests/
+-- api/                    # API route tests
|   +-- security/           # API security tests
|   |   +-- test_authentication_failures.py
|   |   +-- test_injection_attacks.py
|   |   +-- test_rate_limit_enforcement.py
|   +-- test_auth_routes.py
|   +-- test_message_routes.py
|   +-- conftest.py         # API-specific fixtures
+-- auth/                   # Authentication module tests
|   +-- test_login.py
|   +-- test_registration.py
|   +-- test_2fa.py
|   +-- test_sessions.py
+-- messaging/              # Messaging module tests
+-- servers/                # Server management tests
+-- security/               # Core security tests
|   +-- test_xss_prevention.py
|   +-- test_sql_injection.py
|   +-- test_csrf_protection.py
|   +-- test_comprehensive_security.py
+-- unit/                   # Fast unit tests
|   +-- test_validators.py
|   +-- test_property_based_validation.py
|   +-- test_real_hashing.py
+-- fixtures/               # Shared test fixtures
|   +-- database.py         # Database management
|   +-- modules.py          # Module registry
|   +-- security.py         # Security test utilities
|   +-- factories.py        # Entity factories
|   +-- config.py           # Test configuration
+-- conftest.py             # Root fixtures (session-scoped)
+-- pytest.ini              # Pytest configuration
+-- README.md               # This file
```

### Test Categories (Markers)

Tests are automatically marked based on their location:

```python
# Available markers
@pytest.mark.unit           # Fast unit tests, no database
@pytest.mark.integration    # Full module setup required
@pytest.mark.slow           # Intentionally slow (rate limiting)
@pytest.mark.auth           # Authentication tests
@pytest.mark.messaging      # Messaging tests
@pytest.mark.servers        # Server tests
@pytest.mark.api            # API route tests
@pytest.mark.security       # Security tests
```

---

## Running Tests

### Basic Test Execution

```bash
# Run all tests
pytest src/tests/

# Run with verbose output
pytest src/tests/ -v

# Run specific module tests
pytest src/tests/auth/
pytest src/tests/messaging/
pytest src/tests/security/

# Run specific test file
pytest src/tests/auth/test_login.py

# Run specific test function
pytest src/tests/auth/test_login.py::test_successful_login
```

### Running by Marker

```bash
# Run only unit tests (fast)
pytest -m unit

# Run only integration tests
pytest -m integration

# Run authentication tests
pytest -m auth

# Run security tests
pytest -m security

# Exclude slow tests
pytest -m "not slow"

# Combine markers
pytest -m "auth and not slow"
```

### Parallel Execution

```bash
# Run tests in parallel (requires pytest-xdist)
pytest -n auto              # Auto-detect CPU count
pytest -n 4                 # Use 4 workers

# Parallel with specific markers
pytest -m "not slow" -n auto
```

### Coverage Reporting

```bash
# Run with coverage (requires pytest-cov)
pytest --cov=src --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=src --cov-report=html
# Open htmlcov/index.html in browser

# Coverage for specific module
pytest --cov=src.core.auth src/tests/auth/

# Minimum coverage threshold
pytest --cov=src --cov-fail-under=80
```

### Useful Options

```bash
# Stop on first failure
pytest -x

# Show local variables on failure
pytest -l

# Run last failed tests only
pytest --lf

# Run failed tests first, then rest
pytest --ff

# Show test durations
pytest --durations=10

# Verbose with short traceback
pytest -v --tb=short

# Very verbose (show all output)
pytest -vv -s

# Dry run (collect tests without running)
pytest --collect-only
```

---

## Fixture System

### Core Fixtures

#### Session-Scoped Fixtures

```python
# Database and modules (created once per session)
def test_example(modules, session_users):
    auth = modules.auth
    messaging = modules.messaging
    # Use pre-created session users
    user, username, password = session_users[0]
```

#### User Pool Fixtures

```python
# Single user from pool
def test_with_user(user_pool):
    user = user_pool.get_user()

# User with credentials
def test_with_credentials(user_pool):
    user, username, password = user_pool.get_user_with_credentials()

# User with token
def test_with_token(user_pool):
    user, token = user_pool.get_user_with_token()

# Multiple users
def test_with_users(two_users, three_users):
    user1, user2 = two_users
    user1, user2, user3 = three_users
```

#### Factory Fixtures

```python
# User factory (for creating fresh users)
def test_registration(modules, user_factory):
    # Create fresh user (not from pool)
    user = user_factory.create(
        username="testuser",
        email="test@example.com",
        use_pool=False  # Force fresh creation
    )

# Server factory
def test_server_creation(server_factory, user_factory):
    owner = user_factory.create()
    server = server_factory.create(owner=owner, name="Test Server")

    # Server with members
    server, owner, members = server_factory.create_with_members(
        member_count=3
    )

# Conversation factory
def test_messaging(conversation_factory):
    # Create DM
    dm, user1, user2 = conversation_factory.create_dm()

    # Create group
    group, owner, participants = conversation_factory.create_group(
        participant_count=5,
        name="Test Group"
    )
```

#### Manager Fixtures

For tests requiring isolated database state:

```python
# Each manager gets a fresh in-memory database
def test_auth_manager(auth_manager):
    user = auth_manager.register("user", "user@test.com", "Pass123!")

def test_messaging_manager(messaging_manager):
    # Fresh database, no pre-existing data
    pass
```

#### API Testing Fixtures

```python
def test_api_endpoint(test_client, auth_headers):
    response = test_client.get(
        "/api/v1/users/me",
        headers=auth_headers
    )
    assert response.status_code == 200
```

### Custom Fixture Examples

```python
# Create a custom fixture for your test module
@pytest.fixture
def authenticated_users(user_pool, modules):
    """Create multiple authenticated users"""
    users_with_tokens = []
    for _ in range(3):
        user, username, password = user_pool.get_user_with_credentials()
        result = modules.auth.login(username, password)
        users_with_tokens.append((user, result.token))
    return users_with_tokens

# Use in tests
def test_with_authenticated_users(authenticated_users):
    for user, token in authenticated_users:
        # Test authenticated operations
        pass
```

---

## Security Testing

### Security Fixtures

The test suite includes comprehensive security testing utilities in `fixtures/security.py`:

```python
from src.tests.fixtures.security import (
    XSSPayloads,
    SQLInjectionPayloads,
    MalformedInputs,
    AuthenticationPayloads,
    SecurityAssertions,
    SecurityTestHelper
)
```

### XSS Prevention Testing

```python
def test_message_xss_prevention(modules, user_pool, xss_payloads, security_assert):
    """Test that messages are protected against XSS"""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    dm = modules.messaging.create_dm(user1.id, user2.id)

    # Test all XSS payloads
    for payload in xss_payloads.all():
        try:
            msg = modules.messaging.send_message(
                user_id=user1.id,
                conversation_id=dm.id,
                content=payload
            )
            # Assert content was sanitized
            security_assert.assert_no_xss(msg.content, payload)
        except Exception:
            # Rejection is also acceptable
            pass
```

### SQL Injection Testing

```python
def test_login_sql_injection(modules, sql_payloads, security_assert):
    """Test that login is protected against SQL injection"""
    for payload in sql_payloads.all():
        try:
            modules.auth.login(payload, "password")
        except Exception:
            # Should be rejected
            pass
```

### Input Validation Testing

```python
def test_username_validation(modules, malformed_inputs):
    """Test username validation handles malformed inputs"""
    for malformed in malformed_inputs.all():
        with pytest.raises(Exception):
            modules.auth.register(
                username=malformed,
                email="test@test.com",
                password="Pass123!"
            )
```

### Authorization Testing

```python
def test_unauthorized_access(modules, user_pool):
    """Test that users cannot access resources they don't own"""
    owner = user_pool.get_user()
    unauthorized = user_pool.get_user()

    server = modules.servers.create_server(
        owner_id=owner.id,
        name="Private Server"
    )

    # Unauthorized user should not be able to delete
    with pytest.raises(Exception):
        modules.servers.delete_server(
            user_id=unauthorized.id,
            server_id=server.id
        )
```

### Security Test Helper

```python
def test_field_security(modules, user_pool, security_helper, xss_payloads):
    """Use security helper for common patterns"""
    owner = user_pool.get_user()

    # Test XSS in server name field
    security_helper.test_field_xss(
        create_fn=lambda name: modules.servers.create_server(
            owner_id=owner.id,
            name=name
        ),
        field_name="name",
        xss_payloads=xss_payloads
    )
```

### Available Security Payloads

#### XSS Payloads
- Script tags (`<script>alert(1)</script>`)
- Event handlers (`<img onerror=alert(1)>`)
- JavaScript protocol (`javascript:alert(1)`)
- Data URIs, iframes, SVG vectors
- Style injection, HTML entities
- Encoded and polyglot payloads

#### SQL Injection Payloads
- Basic injection (`' OR '1'='1`)
- Union-based attacks
- Stacked queries (`'; DROP TABLE users--`)
- Time-based blind injection
- Boolean-based blind injection
- Error-based injection

#### Malformed Inputs
- Empty values, whitespace
- Extremely long strings
- Unicode edge cases
- Path traversal attempts
- Command injection patterns
- Format strings, invalid JSON
- Negative numbers, overflow values

---

## Test Patterns

### Pattern 1: Testing Registration

```python
def test_user_registration(modules):
    """Test user registration with fresh user"""
    # Always create fresh users for registration tests
    user = modules.auth.register(
        username=f"newuser_{uuid.uuid4().hex[:8]}",
        email=f"newuser_{uuid.uuid4().hex[:8]}@test.com",
        password="SecurePass123!"
    )

    assert user.username.startswith("newuser_")
    assert user.email.endswith("@test.com")
```

### Pattern 2: Testing Message Sending

```python
def test_message_sending(modules, user_pool):
    """Test message sending with pool users"""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()

    dm = modules.messaging.create_dm(user1.id, user2.id)
    msg = modules.messaging.send_message(
        user_id=user1.id,
        conversation_id=dm.id,
        content="Hello!"
    )

    assert msg.content == "Hello!"
    assert msg.author_id == user1.id
```

### Pattern 3: Testing Server Operations

```python
def test_server_creation(modules, user_factory, server_factory):
    """Test server creation with factories"""
    server, owner, members = server_factory.create_with_members(
        member_count=3,
        name="Test Server"
    )

    assert server.owner_id == owner.id
    assert len(members) == 3
```

### Pattern 4: Testing Authentication

```python
def test_login_logout_flow(modules, user_pool):
    """Test complete auth flow"""
    user, username, password = user_pool.get_user_with_credentials()

    # Login
    result = modules.auth.login(username, password)
    assert result.token is not None

    # Verify token
    token_info = modules.auth.verify_token(result.token)
    assert token_info.user_id == user.id

    # Logout
    modules.auth.logout(result.token)

    # Verify token is invalid
    with pytest.raises(Exception):
        modules.auth.verify_token(result.token)
```

### Pattern 5: Testing Permissions

```python
def test_channel_permissions(modules, server_factory, user_factory):
    """Test channel permission system"""
    server, owner, [member] = server_factory.create_with_members(
        member_count=1
    )

    channels = modules.servers.get_channels(owner.id, server.id)
    channel = channels[0]

    # Owner can send messages
    msg = modules.messaging.send_message(
        user_id=owner.id,
        conversation_id=channel.id,
        content="Owner message"
    )
    assert msg is not None

    # Member can send messages (default permission)
    msg = modules.messaging.send_message(
        user_id=member.id,
        conversation_id=channel.id,
        content="Member message"
    )
    assert msg is not None
```

### Pattern 6: Testing Error Cases

```python
def test_invalid_operations(modules, user_pool):
    """Test that invalid operations are properly rejected"""
    user = user_pool.get_user()

    # Test invalid conversation ID
    with pytest.raises(Exception):
        modules.messaging.send_message(
            user_id=user.id,
            conversation_id=999999,
            content="Should fail"
        )

    # Test invalid user ID
    with pytest.raises(Exception):
        modules.auth.get_user(999999)
```

### Pattern 7: Property-Based Testing

```python
from hypothesis import given, strategies as st

@given(
    username=st.text(min_size=3, max_size=32, alphabet=st.characters(
        whitelist_categories=('Ll', 'Lu', 'Nd'),
        whitelist_characters='_-'
    ))
)
def test_username_validation_property(modules, username):
    """Property-based test for username validation"""
    try:
        user = modules.auth.register(
            username=username,
            email=f"{username}@test.com",
            password="Pass123!"
        )
        # If registration succeeds, username should be preserved
        assert user.username == username
    except Exception:
        # Rejection is acceptable for some inputs
        pass
```

---

## Coverage & Quality

### Coverage Targets

- **Overall**: 80%+ line coverage
- **Core modules**: 90%+ line coverage
- **Security code**: 95%+ line coverage
- **API routes**: 85%+ line coverage

### Running Coverage Reports

```bash
# Full coverage report
pytest --cov=src --cov-report=term-missing --cov-report=html

# Coverage for specific module
pytest --cov=src.core.auth --cov-report=term

# Fail if below threshold
pytest --cov=src --cov-fail-under=80
```

### Coverage Configuration

Add to `pytest.ini`:

```ini
[pytest]
addopts =
    --cov=src
    --cov-report=term-missing
    --cov-report=html
    --cov-fail-under=80
```

### Quality Metrics

- **All tests must pass**: No skipped or expected failures
- **No test warnings**: Fix all deprecation warnings
- **Fast execution**: Session setup < 15 seconds, total runtime < 5 minutes
- **Deterministic**: Tests must be repeatable and order-independent
- **Isolated**: Tests must not depend on external services

---

## CI Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3
      with:
        submodules: recursive

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install -r requirements-test.txt

    - name: Run tests with coverage
      run: |
        pytest --cov=src --cov-report=xml --cov-report=term

    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        fail_ci_if_error: true
```

### GitLab CI Example

```yaml
test:
  image: python:3.11
  before_script:
    - pip install -r requirements.txt
    - pip install -r requirements-test.txt
  script:
    - pytest --cov=src --cov-report=term --cov-report=html
  coverage: '/TOTAL.*\s+(\d+%)$/'
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
    paths:
      - htmlcov/
```

### Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
# Run tests before commit

echo "Running tests..."
pytest -x -m "not slow"

if [ $? -ne 0 ]; then
    echo "Tests failed. Commit aborted."
    exit 1
fi

echo "All tests passed!"
exit 0
```

Make executable:
```bash
chmod +x .git/hooks/pre-commit
```

---

## Contributing

### Writing New Tests

1. **Choose the right location**: Place tests in the module-specific directory
2. **Use appropriate fixtures**: Prefer pool users for speed, fresh users when needed
3. **Follow naming conventions**: `test_<feature>_<scenario>.py`
4. **Add docstrings**: Explain what the test validates
5. **Test both success and failure**: Happy path and error cases
6. **Include security tests**: XSS, SQL injection, authorization

### Test Structure Template

```python
"""
Test module for [feature name].

Tests cover:
- Basic functionality
- Error handling
- Edge cases
- Security (XSS, SQL injection, authorization)
- Integration with other modules
"""

import pytest


class TestBasicFunctionality:
    """Test basic [feature] operations."""

    def test_create_item(self, modules, user_pool):
        """Test creating an item."""
        user = user_pool.get_user()
        # Test implementation
        pass

    def test_read_item(self, modules, user_pool):
        """Test reading an item."""
        pass

    def test_update_item(self, modules, user_pool):
        """Test updating an item."""
        pass

    def test_delete_item(self, modules, user_pool):
        """Test deleting an item."""
        pass


class TestErrorHandling:
    """Test error cases and validation."""

    def test_invalid_input(self, modules):
        """Test handling of invalid input."""
        with pytest.raises(Exception):
            # Test that should fail
            pass

    def test_not_found(self, modules, user_pool):
        """Test handling of not found errors."""
        pass


class TestSecurity:
    """Test security aspects."""

    def test_xss_prevention(self, modules, user_pool, xss_payloads):
        """Test XSS prevention."""
        pass

    def test_authorization(self, modules, user_pool):
        """Test authorization checks."""
        pass


class TestIntegration:
    """Test integration with other modules."""

    def test_cross_module_interaction(self, modules, user_pool):
        """Test interaction with other modules."""
        pass
```

### Best Practices

1. **Use descriptive test names**: `test_user_cannot_delete_other_users_messages`
2. **One assertion concept per test**: Focus on testing one thing
3. **Arrange-Act-Assert pattern**: Setup -> Execute -> Verify
4. **Clean up is automatic**: Session-scoped fixtures handle cleanup
5. **Avoid sleeps**: Use deterministic waits or mocks
6. **Test data isolation**: Use unique IDs (`uuid.uuid4().hex[:8]`)
7. **Document complex setups**: Add comments for non-obvious test setup

### Adding New Fixtures

Add module-specific fixtures in `conftest.py` files:

```python
# src/tests/mymodule/conftest.py
import pytest

@pytest.fixture
def my_module_fixture(modules, user_pool):
    """Module-specific fixture."""
    # Setup
    user = user_pool.get_user()
    resource = modules.mymodule.create_resource(user.id)

    yield resource

    # Cleanup (if needed)
    # modules.mymodule.delete_resource(resource.id)
```

### Running Tests During Development

```bash
# Run tests in watch mode (requires pytest-watch)
ptw src/tests/mymodule/

# Run specific test repeatedly
pytest src/tests/mymodule/test_feature.py::test_specific -v

# Debug failing test
pytest src/tests/mymodule/test_feature.py::test_specific -vv -s --pdb
```

### Debugging Tests

```python
# Add breakpoint
def test_something(modules, user_pool):
    user = user_pool.get_user()
    breakpoint()  # Execution will pause here
    result = modules.something.do_thing(user.id)
    assert result is not None

# Run with pdb on failure
pytest --pdb
```

### Common Issues

**Issue**: "User pool exhausted"
- **Solution**: Increase pool size in `conftest.py` or use `user_factory.create(use_pool=False)`

**Issue**: "Database locked"
- **Solution**: Ensure tests don't hold long transactions, use session-scoped DB

**Issue**: "Slow test execution"
- **Solution**: Use pool users instead of creating fresh users, mark slow tests

**Issue**: "Test order dependency"
- **Solution**: Tests should not depend on execution order, use isolated fixtures

---

## Quick Reference

### Common Commands

```bash
# Run all tests
pytest

# Run specific module
pytest src/tests/auth/

# Run with coverage
pytest --cov=src

# Run in parallel
pytest -n auto

# Run marked tests
pytest -m auth

# Run excluding slow tests
pytest -m "not slow"

# Stop on first failure
pytest -x

# Show durations
pytest --durations=10
```

### Common Fixtures

```python
modules          # All modules (lazy loaded)
user_pool        # Pool of pre-created users
user_factory     # Factory for creating users
server_factory   # Factory for creating servers
conversation_factory  # Factory for creating DMs/groups
test_user        # Single user from pool
two_users        # Two users from pool
three_users      # Three users from pool
test_user_with_token  # User with auth token
xss_payloads     # XSS attack vectors
sql_payloads     # SQL injection vectors
malformed_inputs # Malformed input test cases
security_assert  # Security assertion helpers
```

### Useful Markers

```python
@pytest.mark.unit
@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.auth
@pytest.mark.messaging
@pytest.mark.security
```

---

## Additional Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [Hypothesis Documentation](https://hypothesis.readthedocs.io/) (property-based testing)
- [Coverage.py Documentation](https://coverage.readthedocs.io/)
- [OWASP Testing Guide](https://owasp.org/www-project-web-security-testing-guide/)

---

**Last Updated**: 2024
**Test Framework**: pytest 7.0+
**Python Version**: 3.11+
