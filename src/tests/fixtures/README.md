# Test Fixtures

Shared test fixtures and factories.

## Files

- `config.py` - Test configuration fixtures
- `database.py` - Database fixtures
- `factories.py` - Object factories for test data
- `modules.py` - Module setup fixtures
- `security.py` - Security testing utilities and payloads

## Security Testing Utilities

The `security.py` module provides comprehensive security testing utilities:

### Payload Classes

- `XSSPayloads` - XSS attack vectors (script tags, event handlers, polyglots, etc.)
- `SQLInjectionPayloads` - SQL injection patterns (union, stacked queries, blind injection)
- `MalformedInputs` - Edge cases (empty values, unicode, overflow, path traversal)
- `AuthenticationPayloads` - Auth attack vectors (weak passwords, token manipulation)

### Assertion Helpers

`SecurityAssertions` provides methods for security validation:
- `assert_no_xss()` - Verify XSS prevention
- `assert_no_sql_injection()` - Verify SQL injection prevention
- `assert_sanitized()` - Verify dangerous content was sanitized
- `assert_rejected()` - Verify operation was rejected
- `assert_no_error_leakage()` - Verify error messages don't leak info
- `assert_no_path_traversal()` - Verify path traversal prevention
- `assert_content_length_limit()` - Verify length limits
- `assert_rate_limited()` - Verify rate limiting enforcement

### Test Helpers

- `test_xss_vectors()` - Generic XSS testing function
- `test_sql_injection()` - Generic SQL injection testing function
- `test_input_validation()` - Generic input validation testing function
- `SecurityTestHelper` - Class with common security testing patterns

### Usage Examples

```python
def test_message_content_xss(messaging_manager, user_pool, xss_payloads, security_assert):
    """Test XSS prevention in message content."""
    user = user_pool.get_user()
    user2 = user_pool.get_user()
    dm = messaging_manager.create_dm(user.id, user2.id)

    for payload in xss_payloads.SCRIPT_TAGS:
        msg = messaging_manager.send_message(user.id, dm.id, payload)
        security_assert.assert_no_xss(msg.content, payload)

def test_login_sql_injection(auth_manager, sql_payloads):
    """Test SQL injection prevention in login."""
    for payload in sql_payloads.BASIC_INJECTION:
        with pytest.raises(Exception):
            auth_manager.login(payload, "password")

def test_username_validation(auth_manager, malformed_inputs):
    """Test username validation with edge cases."""
    for malformed in malformed_inputs.WHITESPACE:
        with pytest.raises(Exception):
            auth_manager.register(malformed, "test@test.com", "Pass123!")
```
