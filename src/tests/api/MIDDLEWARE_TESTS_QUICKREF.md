# Middleware Tests Quick Reference

## Files Created (Total: ~102KB, 170+ tests)

| File | Size | Tests | Purpose |
|------|------|-------|---------|
| `test_middleware_authentication.py` | 21KB | ~40 | Authentication middleware tests |
| `test_middleware_error_handling.py` | 19KB | ~35 | Error handling middleware tests |
| `test_middleware_logging.py` | 18KB | ~30 | Logging middleware tests |
| `test_middleware_rate_limiting.py` | 24KB | ~40 | Rate limiting middleware tests |
| `test_middleware_integration.py` | 20KB | ~25 | Integration tests for all middleware |

## Quick Test Commands

```bash
# Run all middleware tests
pytest src/tests/api/test_middleware_*.py -v

# Run with coverage
pytest src/tests/api/test_middleware_*.py --cov=src/api/middleware --cov-report=html

# Run specific security tests
pytest src/tests/api/test_middleware_authentication.py::TestSecurityScenarios -v
pytest src/tests/api/test_middleware_error_handling.py::TestSecurityAndInformationLeakage -v

# Run performance tests
pytest src/tests/api/test_middleware_logging.py::TestPerformance -v

# Run integration tests only
pytest src/tests/api/test_middleware_integration.py -v
```

## Test Coverage Summary

### Authentication (test_middleware_authentication.py)
- ✅ 8 test classes, ~40 test methods
- ✅ Token validation (Bearer, Bot, expired, revoked, invalid)
- ✅ Security scenarios (SQL injection, XSS, long tokens)
- ✅ Concurrent requests (50+ simultaneous)
- ✅ Dependencies (get_current_user, get_optional_user)

### Error Handling (test_middleware_error_handling.py)
- ✅ 7 test classes, ~35 test methods
- ✅ 16 exception types mapped to HTTP status codes
- ✅ CORS headers on errors
- ✅ No information leakage (secrets, paths, stack traces)
- ✅ Validation errors

### Logging (test_middleware_logging.py)
- ✅ 8 test classes, ~30 test methods
- ✅ Timing accuracy (millisecond precision)
- ✅ Log levels (info/warning/error based on status)
- ✅ Skipped paths (/health, /favicon.ico)
- ✅ Telemetry integration

### Rate Limiting (test_middleware_rate_limiting.py)
- ✅ 8 test classes, ~40 test methods
- ✅ User info extraction (IP, admin, bot detection)
- ✅ Rate limit enforcement
- ✅ Bypass functionality (admin, internal)
- ✅ Security (brute force protection)

### Integration (test_middleware_integration.py)
- ✅ 6 test classes, ~25 test methods
- ✅ All middleware together
- ✅ Real-world scenarios (login protection, concurrent users)
- ✅ Error propagation through stack

## Key Features Tested

### Security ✅
- SQL injection prevention
- XSS prevention
- Token validation
- Brute force protection
- Information leakage prevention
- Session hijacking prevention

### Performance ✅
- Concurrent request handling (50+ requests)
- Minimal middleware overhead
- Thread safety
- Rate limit efficiency

### Reliability ✅
- Error propagation
- Exception handling
- Edge cases (None, empty, unicode, long input)
- Malformed input

### Compliance ✅
- HTTP standards
- CORS headers
- Status codes
- Error formats
- Rate limit headers

## Common Test Patterns

### Testing with Authentication
```python
def test_with_auth(self, modules, test_user_with_token):
    user, token = test_user_with_token
    headers = {"Authorization": f"Bearer {token}"}
    # Make authenticated request
```

### Testing Rate Limiting
```python
@pytest.fixture
def rate_limited_app(self):
    storage = MemoryStorage()
    ratelimit.setup(storage_backend=storage, ...)
    # Create app with rate limiting
    yield app
    ratelimit._manager = None  # Cleanup
```

### Testing with Mocks
```python
@patch('src.api.middleware.logging._log_info')
def test_logging(self, mock_log, app):
    # Test and verify logging
    assert mock_log.called
```

## Expected Test Results

- ✅ All 170+ tests should pass
- ✅ Coverage >95% for middleware files
- ✅ Test execution time <30 seconds
- ✅ No warnings or errors
- ✅ No flaky tests (all tests are deterministic)

## Troubleshooting

### Tests fail with "rate limiting already setup"
```python
# Add cleanup in fixture:
yield app
ratelimit._manager = None
ratelimit._setup_complete = False
```

### Tests fail with "auth module not available"
```python
# Use session-scoped modules fixture:
def test_something(self, modules):
    auth = modules.auth
```

### Async tests not running
```python
# Mark with pytest.mark.asyncio:
@pytest.mark.asyncio
async def test_async_feature(self):
    ...
```

## Documentation

- **Full Documentation**: See `MIDDLEWARE_TESTS.md`
- **Implementation Summary**: See `middleware_test_summary.txt`
- **Middleware Code**: See `src/api/middleware/`
