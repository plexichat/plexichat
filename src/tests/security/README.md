# Security Test Suite

Comprehensive security testing for PlexiChat covering all major security concerns.

## Test Coverage

### 1. XSS Prevention (`test_xss_prevention.py`)
- Script tag injection
- Event handler attributes (onclick, onerror, etc.)
- JavaScript protocol URLs
- Iframe injection
- SVG with embedded scripts
- HTML entities encoding
- Data URIs with scripts
- Nested XSS payloads
- URL-encoded script tags
- Style tags with expressions
- XSS in usernames, server names, channel names, and group names

### 2. SQL Injection Prevention (`test_sql_injection.py`)
- SQL injection in login credentials
- SQL injection in registration fields
- SQL injection in message content
- SQL injection in search queries
- SQL injection in server/channel names
- UNION-based SQL injection
- Time-based blind SQL injection
- Boolean-based blind SQL injection
- Stacked queries injection
- Comment-based injection
- Quote escaping attempts
- Batch operation injection

### 3. CSRF Protection (`test_csrf_protection.py`)
- Authentication requirements for state changes
- Token validation for operations
- User identity verification
- Cross-user operation prevention
- Session token validation
- Expired session rejection
- Revoked session rejection
- Ownership validation
- Permission checks
- Conversation membership validation
- Password change protection
- 2FA operation protection
- Bot operation authorization
- Session binding

### 4. Authentication Bypass (`test_authentication_bypass.py`)
- Invalid token rejection
- Malformed token rejection
- Token tampering detection
- Session ID tampering
- Password brute force protection
- Account lockout
- Bot token restrictions
- Replay attack prevention
- Timing attack resistance
- Null byte injection
- Unicode normalization attacks
- Empty credential rejection
- Special character handling
- Password reset token single-use

### 5. Authorization Checks (`test_authorization.py`)
- Message access control
- Message editing authorization
- Message deletion authorization
- Server access control
- Channel access control
- Member kick authorization
- Profile modification protection
- Session access control
- Bot management authorization
- Group creation ownership
- Permission enforcement
- Bot permission restrictions
- Permission escalation prevention
- Conversation participant validation
- Channel access validation
- Invite code validation
- Role-based permissions

### 6. Session Hijacking Prevention (`test_session_hijacking.py`)
- Unique session tokens
- Token randomness
- Token unpredictability
- Session expiration
- Session invalidation on logout
- All sessions revocation
- Session refresh security
- Session fixation prevention
- IP binding (if enabled)
- User agent binding (if enabled)
- Concurrent session limits
- Session activity tracking
- Device tracking
- Suspicious activity detection
- Session takeover detection
- Token reuse detection
- Device revocation
- Password change session handling

### 7. Token Validation (`test_token_validation.py`)
- Valid token acceptance
- Token format validation
- Token secret validation
- Token ID validation
- Expired token rejection
- Revoked token rejection
- Token type validation
- Bot token format
- Session token format
- Token minimum entropy
- Token character set validation
- Token rate limiting
- Token reuse detection
- Token permissions validation
- Disabled bot token rejection
- Token regeneration
- Token validation caching
- Cache invalidation
- Malformed token handling
- Null byte handling
- Unicode handling
- Timing attack resistance

### 8. API Security (`test_api_security.py`)
- Authentication requirements
- User ID validation
- Message ID validation
- Conversation ID validation
- Content length validation
- Username format validation
- Email format validation
- Password requirements validation
- Rate limiting enforcement
- Error message sanitization
- Missing parameter handling
- Wrong type parameter handling
- Parameter pollution prevention
- JSON structure validation
- Nested object depth limits
- Array bounds validation
- Error response sanitization
- Mass assignment prevention
- File upload validation
- Path traversal prevention
- Unicode handling

### 9. Comprehensive Security (`test_comprehensive_security.py`)

#### Data Protection
- Password exposure prevention
- Token logging protection
- Session token hashing
- Bot token hashing
- 2FA secret encryption
- Backup code hashing
- Email privacy
- IP address security
- Audit logging

#### Race Conditions
- Concurrent login attempts
- Concurrent message sends
- Concurrent session creation

#### Input Boundaries
- Maximum username length
- Minimum username length
- Maximum message length
- Zero-length inputs
- Negative numeric inputs
- Extremely large numeric inputs

#### Security Headers
- Token expiration enforcement
- Session security attributes

#### Privilege Escalation
- Admin permission restriction
- Bot admin permission restriction
- Member to owner escalation prevention
- Permission inheritance security

#### Denial of Service
- Message rate limiting
- Login attempt rate limiting
- Resource exhaustion prevention

## Running the Tests

### Run all security tests:
```bash
pytest src/tests/security/
```

### Run specific test file:
```bash
pytest src/tests/security/test_xss_prevention.py
pytest src/tests/security/test_sql_injection.py
pytest src/tests/security/test_csrf_protection.py
```

### Run with verbose output:
```bash
pytest src/tests/security/ -v
```

### Run with coverage:
```bash
pytest src/tests/security/ --cov=src/core --cov=src/api
```

## Test Dependencies

The security tests use the shared test fixtures from `conftest.py`:
- `modules` - Access to all core modules (auth, messaging, servers, etc.)
- `user_pool` - Pool of pre-created test users
- `db_manager` - Database manager for direct DB access

## Security Best Practices Tested

1. **Input Validation**: All user input is validated and sanitized
2. **Output Encoding**: HTML/JavaScript content is properly encoded
3. **Parameterized Queries**: SQL injection prevention through parameterization
4. **Authentication**: Proper token-based authentication
5. **Authorization**: Permission checks before operations
6. **Session Management**: Secure session handling with timeouts
7. **Token Security**: Cryptographically secure tokens with hashing
8. **Rate Limiting**: Protection against brute force and DoS
9. **Error Handling**: Safe error messages without information leakage
10. **Encryption**: Sensitive data encryption at rest

## Adding New Security Tests

When adding new features, ensure security tests cover:

1. **Input validation** for all parameters
2. **Authentication** requirements for protected operations
3. **Authorization** checks for resource access
4. **XSS prevention** for any user-generated content
5. **SQL injection prevention** for database queries
6. **CSRF protection** for state-changing operations
7. **Rate limiting** for resource-intensive operations

## Security Markers

Tests are automatically marked with `@pytest.mark.integration` and can be filtered:

```bash
pytest -m integration src/tests/security/
```

## Continuous Security Testing

These tests should be run:
- Before every commit
- In CI/CD pipeline
- Before every release
- After any security-related code changes
- Regularly as part of security audits
