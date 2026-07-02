# Middleware Test Suite

Comprehensive test suite for all middleware in `src/api/middleware/`.

## Test Files

### test_middleware_authentication.py
Tests for authentication middleware covering:
- **Token Validation**: Bearer and Bot token schemes, case insensitivity
- **Security Scenarios**: Expired tokens, revoked tokens, invalid tokens, SQL injection, XSS attempts
- **Dependencies**: `get_current_user` and `get_optional_user` functions
- **Concurrency**: Thread-safe authentication under load
- **Edge Cases**: Malformed headers, missing auth module, empty tokens
- **IP & User Agent**: Extraction and validation

**Key Test Classes:**
- `TestAuthenticationMiddleware`: Core middleware functionality
- `TestGetCurrentUserDependency`: Required authentication dependency
- `TestGetOptionalUserDependency`: Optional authentication
- `TestSecurityScenarios`: Security-focused tests
- `TestConcurrency`: Concurrent request handling
- `TestIPAddressAndUserAgent`: Request metadata handling

### test_middleware_error_handling.py
Tests for error handling middleware covering:
- **Exception Mapping**: NotFoundError->404, AccessDeniedError->403, etc.
- **Response Formatting**: Consistent error response structure
- **CORS Headers**: Proper CORS headers on error responses
- **Validation Errors**: Pydantic validation error handling
- **Security**: No information leakage (stack traces, file paths, secrets)
- **Edge Cases**: Unicode, very long messages, None/empty exceptions

**Key Test Classes:**
- `TestStatusCodeMapping`: Exception to HTTP status code mapping
- `TestErrorResponseFormatting`: Error response structure
- `TestExceptionHandlers`: FastAPI exception handlers
- `TestCORSHeaders`: CORS header inclusion
- `TestSecurityAndInformationLeakage`: Security tests
- `TestValidationErrors`: Pydantic validation
- `TestEdgeCases`: Edge case handling

### test_middleware_logging.py
Tests for logging middleware covering:
- **Request/Response Logging**: All HTTP methods logged correctly
- **Timing Accuracy**: Accurate millisecond timing measurements
- **Log Levels**: Info for 2xx, warning for 4xx, error for 5xx
- **Skipped Paths**: Health checks and favicon not logged
- **Telemetry Integration**: Server-side telemetry submission
- **Exception Logging**: Errors during request processing
- **Performance**: Minimal overhead under load

**Key Test Classes:**
- `TestLoggingMiddleware`: Core logging functionality
- `TestSkippedPaths`: Path exclusion logic
- `TestTelemetryIntegration`: Telemetry submission
- `TestExceptionLogging`: Exception handling
- `TestDifferentHTTPMethods`: All HTTP methods
- `TestStatusCodeRanges`: Different status codes
- `TestPerformance`: Performance benchmarks

### test_middleware_rate_limiting.py
Tests for rate limiting middleware covering:
- **User Info Extraction**: User ID, IP, admin status, bot detection
- **Rate Limit Enforcement**: Request counting and blocking
- **Bypass Functionality**: Admin, internal, bot bypasses
- **Header Inclusion**: X-RateLimit-* headers
- **IP-Based Limiting**: Unauthenticated request limiting
- **Security**: Brute force protection, injection prevention
- **Integration**: Works with authentication middleware

**Key Test Classes:**
- `TestGetUserInfoFromRequest`: User info extraction
- `TestCreateRateLimitMiddleware`: Middleware creation
- `TestRateLimitEnforcement`: Rate limit enforcement
- `TestBypassFunctionality`: Bypass mechanisms
- `TestIPBasedRateLimiting`: IP-based limits
- `TestSecurityScenarios`: Security tests
- `TestIntegrationWithAuthentication`: Auth integration
- `TestEdgeCases`: Edge cases

### test_middleware_integration.py
Integration tests for all middleware working together:
- **Execution Order**: Proper middleware ordering
- **Combined Scenarios**: Auth + RateLimit + Logging + ErrorHandling
- **Real-World Workflows**: Login protection, user endpoints
- **Concurrent Users**: Multiple users with separate limits
- **Error Propagation**: Errors through entire stack
- **Admin Privileges**: Admin bypass with all middleware active

**Key Test Classes:**
- `TestMiddlewareExecutionOrder`: Middleware ordering
- `TestAuthenticationWithRateLimiting`: Auth + rate limit
- `TestAuthenticationWithErrorHandling`: Auth + errors
- `TestLoggingWithErrorHandling`: Logging + errors
- `TestAllMiddlewareTogether`: Full stack tests
- `TestRealWorldScenarios`: Complex scenarios
- `TestErrorPropagation`: Error handling through stack

## Running Tests

### Run all middleware tests
```bash
pytest src/tests/api/test_middleware_*.py -v
```

### Run specific test file
```bash
pytest src/tests/api/test_middleware_authentication.py -v
pytest src/tests/api/test_middleware_error_handling.py -v
pytest src/tests/api/test_middleware_logging.py -v
pytest src/tests/api/test_middleware_rate_limiting.py -v
pytest src/tests/api/test_middleware_integration.py -v
```

### Run specific test class
```bash
pytest src/tests/api/test_middleware_authentication.py::TestSecurityScenarios -v
```

### Run specific test
```bash
pytest src/tests/api/test_middleware_authentication.py::TestSecurityScenarios::test_sql_injection_in_token -v
```

### Run with coverage
```bash
pytest src/tests/api/test_middleware_*.py --cov=src/api/middleware --cov-report=html
```

## Test Coverage

### Authentication Middleware
- OK Token extraction (Bearer, Bot schemes)
- OK Token validation (valid, expired, revoked, invalid)
- OK User state management
- OK IP address and user agent handling
- OK Security scenarios (SQL injection, XSS, long tokens)
- OK Concurrent request handling
- OK Dependencies (get_current_user, get_optional_user)
- OK Error cases (missing auth module, malformed headers)

### Error Handling Middleware
- OK Exception to status code mapping (all ERROR_MAPPINGS)
- OK Error response formatting
- OK HTTP exception handling
- OK Validation error handling
- OK CORS headers on errors
- OK Security (no information leakage)
- OK Edge cases (unicode, long messages, None/empty)

### Logging Middleware
- OK Request/response logging
- OK Timing accuracy
- OK Log level selection
- OK Skipped paths
- OK Telemetry integration
- OK Exception logging
- OK Different HTTP methods
- OK Different status code ranges
- OK Performance under load

### Rate Limiting Middleware
- OK User info extraction
- OK IP address extraction (client, X-Forwarded-For)
- OK Admin detection (admin.*, *, admin permissions)
- OK Bot detection
- OK Internal request detection
- OK Rate limit enforcement
- OK Bypass functionality
- OK Header inclusion
- OK Security scenarios
- OK Integration with authentication

### Integration Tests
- OK Middleware execution order
- OK Auth + Rate Limiting
- OK Auth + Error Handling
- OK Logging + Error Handling
- OK All middleware together
- OK Real-world scenarios
- OK Concurrent users
- OK Error propagation

## Test Statistics

- **Total Test Files**: 5
- **Total Test Classes**: ~35
- **Total Test Methods**: ~200+
- **Coverage Areas**:
  - Authentication: ~40 tests
  - Error Handling: ~35 tests
  - Logging: ~30 tests
  - Rate Limiting: ~40 tests
  - Integration: ~25 tests

## Key Features Tested

### Security
- SQL injection prevention
- XSS prevention
- Token validation
- Brute force protection
- Information leakage prevention
- Session hijacking prevention

### Performance
- Concurrent request handling
- Minimal middleware overhead
- Thread safety
- Rate limit efficiency

### Reliability
- Error propagation
- Exception handling
- Edge cases
- Null/empty values
- Malformed input

### Compliance
- HTTP standards
- CORS headers
- Status codes
- Error formats

## Dependencies

All tests use:
- `pytest` for test framework
- `FastAPI TestClient` for HTTP testing
- `httpx.AsyncClient` for async tests
- `unittest.mock` for mocking
- Session-scoped fixtures from `conftest.py`

## Notes

- Tests use real database and authentication (session-scoped)
- Rate limiting tests clean up after themselves
- Mock logging to avoid test pollution
- All tests are independent and can run in parallel
- Tests follow AAA pattern (Arrange, Act, Assert)
