# Middleware Test Suite - Index

## 📁 Test Files (5 files, ~102KB, 170+ tests)

### Core Test Files
1. **[test_middleware_authentication.py](test_middleware_authentication.py)** (21KB, 40 tests)
   - Authentication middleware functionality
   - Token validation and extraction
   - Security scenarios (SQL injection, XSS)
   - Concurrent request handling
   - Dependencies: `get_current_user`, `get_optional_user`

2. **[test_middleware_error_handling.py](test_middleware_error_handling.py)** (19KB, 35 tests)
   - Exception to HTTP status code mapping
   - Error response formatting
   - CORS headers on errors
   - Security: no information leakage
   - Validation error handling

3. **[test_middleware_logging.py](test_middleware_logging.py)** (18KB, 30 tests)
   - Request/response logging
   - Timing accuracy (millisecond precision)
   - Log level selection (info/warning/error)
   - Skipped paths and telemetry integration
   - Performance under load

4. **[test_middleware_rate_limiting.py](test_middleware_rate_limiting.py)** (24KB, 40 tests)
   - User info extraction (IP, admin, bot)
   - Rate limit enforcement
   - Bypass functionality (admin, internal)
   - Security: brute force protection
   - Integration with authentication

5. **[test_middleware_integration.py](test_middleware_integration.py)** (20KB, 25 tests)
   - All middleware working together
   - Middleware execution order
   - Real-world scenarios
   - Error propagation through stack
   - Concurrent users with separate limits

### Documentation Files
- **[MIDDLEWARE_TESTS.md](MIDDLEWARE_TESTS.md)** - Complete documentation
- **[MIDDLEWARE_TESTS_QUICKREF.md](MIDDLEWARE_TESTS_QUICKREF.md)** - Quick reference
- **[middleware_test_summary.txt](middleware_test_summary.txt)** - Implementation summary
- **[MIDDLEWARE_INDEX.md](MIDDLEWARE_INDEX.md)** - This file

## 🎯 Coverage by Middleware

| Middleware | File | Tests | Coverage |
|------------|------|-------|----------|
| Authentication | `authentication.py` | 40 | Token validation, dependencies, security |
| Error Handling | `error_handling.py` | 35 | Exception mapping, formatting, CORS |
| Logging | `logging.py` | 30 | Request/response, timing, telemetry |
| Rate Limiting | `rate_limiting.py` | 40 | Enforcement, bypass, user extraction |
| Integration | All | 25 | Combined scenarios, ordering, propagation |

## 🚀 Quick Start

### Run All Tests
```bash
pytest src/tests/api/test_middleware_*.py -v
```

### Run Specific Test File
```bash
pytest src/tests/api/test_middleware_authentication.py -v
```

### Run with Coverage
```bash
pytest src/tests/api/test_middleware_*.py --cov=src/api/middleware --cov-report=html
```

### Run Specific Test Class
```bash
pytest src/tests/api/test_middleware_authentication.py::TestSecurityScenarios -v
```

## 📊 Test Statistics

- **Total Test Files**: 5
- **Total Test Classes**: 35+
- **Total Test Methods**: 170+
- **Total Lines of Code**: ~2,800+
- **Total File Size**: ~102KB

### Breakdown by Category
- **Security Tests**: ~50 tests
- **Functionality Tests**: ~70 tests
- **Integration Tests**: ~25 tests
- **Performance Tests**: ~10 tests
- **Edge Case Tests**: ~15 tests

## 🔍 Test Classes by File

### test_middleware_authentication.py
1. `TestAuthenticationMiddleware` - Core middleware
2. `TestGetCurrentUserDependency` - Required auth
3. `TestGetOptionalUserDependency` - Optional auth
4. `TestSecurityScenarios` - Security tests
5. `TestConcurrency` - Concurrent requests
6. `TestIPAddressAndUserAgent` - Metadata handling

### test_middleware_error_handling.py
1. `TestStatusCodeMapping` - Exception mapping
2. `TestErrorResponseFormatting` - Response format
3. `TestExceptionHandlers` - Handler tests
4. `TestCORSHeaders` - CORS on errors
5. `TestSecurityAndInformationLeakage` - Security
6. `TestValidationErrors` - Pydantic validation
7. `TestEdgeCases` - Edge cases

### test_middleware_logging.py
1. `TestLoggingMiddleware` - Core logging
2. `TestSkippedPaths` - Path exclusion
3. `TestTelemetryIntegration` - Telemetry
4. `TestExceptionLogging` - Exception logging
5. `TestDifferentHTTPMethods` - HTTP methods
6. `TestStatusCodeRanges` - Status codes
7. `TestWebSocketRequests` - WebSocket handling
8. `TestPerformance` - Performance tests

### test_middleware_rate_limiting.py
1. `TestGetUserInfoFromRequest` - User extraction
2. `TestCreateRateLimitMiddleware` - Creation
3. `TestRateLimitEnforcement` - Enforcement
4. `TestBypassFunctionality` - Bypass logic
5. `TestIPBasedRateLimiting` - IP limiting
6. `TestSecurityScenarios` - Security
7. `TestIntegrationWithAuthentication` - Auth integration
8. `TestEdgeCases` - Edge cases

### test_middleware_integration.py
1. `TestMiddlewareExecutionOrder` - Ordering
2. `TestAuthenticationWithRateLimiting` - Auth + rate limit
3. `TestAuthenticationWithErrorHandling` - Auth + errors
4. `TestLoggingWithErrorHandling` - Logging + errors
5. `TestAllMiddlewareTogether` - Full stack
6. `TestRealWorldScenarios` - Real scenarios
7. `TestErrorPropagation` - Error propagation

## 🔒 Security Testing Coverage

### Covered Attack Vectors
- ✅ SQL Injection (in tokens, headers)
- ✅ XSS Attacks (in tokens, error messages)
- ✅ Token Reuse (after logout/revocation)
- ✅ Brute Force (rate limiting on login)
- ✅ Session Hijacking (token validation)
- ✅ Information Leakage (error details, stack traces)
- ✅ Path Traversal (via rate limiting)
- ✅ Header Injection (malicious headers)

### Security Test Examples
```bash
# Authentication security
pytest src/tests/api/test_middleware_authentication.py::TestSecurityScenarios -v

# Error handling security
pytest src/tests/api/test_middleware_error_handling.py::TestSecurityAndInformationLeakage -v

# Rate limiting security
pytest src/tests/api/test_middleware_rate_limiting.py::TestSecurityScenarios -v
```

## 📈 Performance Testing

### Performance Tests Included
- ✅ Concurrent requests (50+ simultaneous)
- ✅ Minimal overhead measurement (<5s for 100 requests)
- ✅ Thread safety verification
- ✅ Rate limit efficiency
- ✅ Timing accuracy (millisecond precision)

### Run Performance Tests
```bash
pytest src/tests/api/test_middleware_logging.py::TestPerformance -v
pytest src/tests/api/test_middleware_authentication.py::TestConcurrency -v
```

## 🛠️ Common Use Cases

### Testing New Middleware
1. Create `test_middleware_<name>.py`
2. Add test classes for each feature
3. Include security, performance, and edge cases
4. Add integration tests to `test_middleware_integration.py`

### Debugging Failed Tests
1. Run specific test: `pytest path/to/test.py::TestClass::test_method -v`
2. Add `-s` flag to see print statements
3. Use `--pdb` to drop into debugger on failure
4. Check fixture cleanup (especially rate limiting)

### Adding New Test Cases
1. Follow existing patterns in test files
2. Use appropriate fixtures (modules, test_user_with_token, etc.)
3. Mock logging/telemetry to avoid pollution
4. Clean up rate limiting after tests
5. Test both success and failure paths

## 📚 Additional Resources

### Related Files
- **Middleware Source**: `src/api/middleware/`
- **Main Test Config**: `src/tests/conftest.py`
- **API Tests**: `src/tests/api/`
- **Rate Limit Tests**: `src/tests/ratelimit/`

### Documentation
- **AGENTS.md**: See project setup and commands
- **API README**: `src/api/README.md`
- **Middleware README**: `src/api/middleware/README.md`

## ✅ Checklist for Running Tests

- [ ] Ensure test environment is set up (see AGENTS.md)
- [ ] Install test dependencies: `pip install -r requirements-test.txt`
- [ ] Run all tests: `pytest src/tests/api/test_middleware_*.py -v`
- [ ] Check coverage: `pytest --cov=src/api/middleware --cov-report=html`
- [ ] Review coverage report: Open `htmlcov/index.html`
- [ ] Verify all tests pass (170+ tests)
- [ ] Check for no warnings or errors
- [ ] Verify execution time (<30 seconds)

## 🎓 Learning Resources

### Understanding the Tests
1. Start with `test_middleware_authentication.py` (most straightforward)
2. Review `test_middleware_integration.py` for real-world examples
3. Check `MIDDLEWARE_TESTS.md` for detailed documentation
4. See `MIDDLEWARE_TESTS_QUICKREF.md` for quick reference

### Test Patterns Used
- **AAA Pattern**: Arrange, Act, Assert
- **Fixtures**: Session-scoped for performance
- **Mocking**: For logging and telemetry
- **Parametrization**: For testing multiple scenarios
- **Markers**: For categorizing tests

---

**Created**: 2024
**Total Implementation Time**: ~2 hours
**Lines of Code**: ~2,800+
**Test Coverage**: 170+ tests across 5 files
**Status**: ✅ Complete and ready for use
