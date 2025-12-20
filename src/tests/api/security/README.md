# API Endpoint Security Tests

Comprehensive security testing suite for all API routes in `src/api/routes/`.

## Overview

This test suite provides extensive security testing coverage for the PlexiChat API, ensuring that all endpoints are properly protected against common web vulnerabilities and attack vectors.

## Test Categories

### 1. Authentication Failures (`test_authentication_failures.py`)
Tests that all protected endpoints properly reject:
- Missing authentication tokens
- Invalid/malformed tokens
- Expired tokens
- Revoked tokens
- Token injection attempts
- Case manipulation attempts

**Key Test Classes:**
- `TestAuthenticationRequired` - Verifies auth requirements on all protected routes
- `TestInvalidTokens` - Tests rejection of various invalid token formats
- `TestRevokedTokens` - Ensures revoked tokens cannot be reused
- `TestTokenInjection` - Tests token manipulation attempts

### 2. Permission Checks (`test_permission_checks.py`)
Tests that users cannot:
- Access resources they don't own
- Modify resources without proper permissions
- View private data of other users
- Escalate privileges through parameter tampering

**Key Test Classes:**
- `TestResourceOwnership` - Verifies ownership checks on resources
- `TestPrivateDataAccess` - Ensures private data is protected
- `TestServerPermissions` - Tests server-specific permission checks
- `TestCrossUserOperations` - Validates cross-user operation restrictions
- `TestParameterTampering` - Tests privilege escalation attempts

### 3. Rate Limit Enforcement (`test_rate_limit_enforcement.py`)
Tests that rate limits are properly enforced for:
- Authentication endpoints (login, register, 2FA)
- Message sending and editing
- Friend requests and blocking
- Server creation
- Reaction additions
- Profile updates

**Key Test Classes:**
- `TestAuthenticationRateLimits` - Auth endpoint rate limits
- `TestMessagingRateLimits` - Message operation rate limits
- `TestRelationshipRateLimits` - Friend/block rate limits
- `TestServerRateLimits` - Server operation rate limits
- `TestReactionRateLimits` - Reaction rate limits
- `TestProfileUpdateRateLimits` - Profile update rate limits
- `TestRateLimitHeaders` - Rate limit header validation

### 4. Header Injection (`test_header_injection.py`)
Tests protection against:
- CRLF injection in headers
- Malicious header values
- Oversized headers
- Authorization header manipulation
- Custom header validation

**Key Test Classes:**
- `TestHeaderInjection` - CRLF injection attempts
- `TestMaliciousHeaders` - Various malicious header payloads
- `TestAuthorizationHeaderManipulation` - Auth header tampering
- `TestCustomHeaderValidation` - Custom header handling
- `TestContentSecurityHeaders` - Security header validation

### 5. Parameter Tampering (`test_parameter_tampering.py`)
Tests validation and sanitization of:
- Request body parameters
- Query parameters
- Path parameters
- Array and object bounds
- Type validation
- Special characters

**Key Test Classes:**
- `TestBodyParameterValidation` - Body parameter validation
- `TestContentLengthValidation` - Content length limits
- `TestArrayBoundsValidation` - Array size validation
- `TestTypeValidation` - Type checking
- `TestSpecialCharacterHandling` - Unicode and special chars
- `TestQueryParameterValidation` - Query parameter validation

### 6. CORS Validation (`test_cors_validation.py`)
Tests that CORS is properly configured:
- CORS headers are set correctly
- Origin validation is performed
- Preflight requests are handled
- Credentials are properly controlled
- Security issues are prevented

**Key Test Classes:**
- `TestCORSHeaders` - CORS header presence and values
- `TestOriginValidation` - Origin validation logic
- `TestCORSMethods` - Allowed HTTP methods
- `TestCORSAllowedHeaders` - Allowed request headers
- `TestCORSSecurityIssues` - CORS security considerations

### 7. Injection Attacks (`test_injection_attacks.py`)
Tests prevention of:
- SQL injection
- NoSQL injection
- Command injection
- XSS (Cross-Site Scripting)
- Path traversal
- LDAP injection
- XPath injection

**Key Test Classes:**
- `TestSQLInjection` - SQL injection prevention
- `TestNoSQLInjection` - NoSQL injection prevention
- `TestCommandInjection` - Command injection prevention
- `TestXSSPrevention` - XSS attack prevention
- `TestPathTraversal` - Path traversal prevention
- `TestLDAPInjection` - LDAP injection prevention
- `TestXPathInjection` - XPath injection prevention

## Running the Tests

### Run all security tests:
```bash
pytest src/tests/api/security/
```

### Run specific test category:
```bash
pytest src/tests/api/security/test_authentication_failures.py
pytest src/tests/api/security/test_permission_checks.py
pytest src/tests/api/security/test_rate_limit_enforcement.py
```

### Run with specific markers:
```bash
# Run only slow tests (rate limit tests)
pytest src/tests/api/security/ -m slow

# Skip slow tests
pytest src/tests/api/security/ -m "not slow"
```

### Run with verbose output:
```bash
pytest src/tests/api/security/ -v
```

## Test Fixtures

The security tests use several shared fixtures defined in `conftest.py`:

- `malicious_payloads` - Common injection payloads (SQL, XSS, path traversal, etc.)
- `invalid_tokens` - Invalid authentication tokens for testing
- `create_user_with_token` - Factory to create users with auth tokens
- `create_server_with_owner` - Factory to create servers with owners

## Coverage

These tests provide comprehensive coverage for all routes in:
- `src/api/routes/auth.py` - Authentication routes
- `src/api/routes/users.py` - User profile routes
- `src/api/routes/servers.py` - Server management routes
- `src/api/routes/channels.py` - Channel routes
- `src/api/routes/messages.py` - Message CRUD routes
- `src/api/routes/relationships.py` - Friend/block routes
- `src/api/routes/presence.py` - Presence routes
- `src/api/routes/reactions.py` - Reaction routes
- `src/api/routes/webhooks.py` - Webhook routes
- `src/api/routes/notifications.py` - Notification routes
- And all other routes in the API

## Security Considerations

These tests ensure:
1. **Authentication** - All protected endpoints require valid authentication
2. **Authorization** - Users can only access resources they have permission for
3. **Input Validation** - All user inputs are properly validated and sanitized
4. **Rate Limiting** - Abuse prevention through rate limits
5. **Injection Prevention** - Protection against all common injection attacks
6. **CORS Security** - Proper CORS configuration without vulnerabilities
7. **Header Security** - Protection against header injection attacks
8. **Data Privacy** - Private user data is not exposed

## Contributing

When adding new API routes:
1. Add corresponding security tests in the appropriate test file
2. Ensure all authentication, authorization, and input validation scenarios are covered
3. Test for rate limiting if applicable
4. Verify protection against injection attacks
5. Run the full security test suite to ensure no regressions

## Notes

- Some rate limit tests are marked as `@pytest.mark.slow` as they intentionally trigger rate limits
- Tests use real database operations through the module system
- Tests create isolated data using factory fixtures to avoid test interdependencies
