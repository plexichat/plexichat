# Logger Tests

Unit tests for the Logger utility.

## Test Coverage

- `test_logger.py` - Tests for logger creation, log content, zipping, custom formats, and rotation

## Key Test Cases

- Logger initialization and directory creation
- Log message content verification
- Old log file zipping
- Custom log name formats
- Log rotation with size limits


### test_sanitizer.py

Tests for the log sanitizer functionality that removes sensitive information from logs.

**Test Cases:**
- `test_sanitize_password()` - Verifies password sanitization in log messages
- `test_sanitize_token()` - Verifies API token sanitization
- `test_sanitize_email()` - Verifies email address sanitization
- `test_sanitize_credit_card()` - Verifies credit card number sanitization
- `test_sanitize_multiple()` - Verifies sanitization of multiple sensitive fields
- `test_sanitize_nested()` - Verifies sanitization in nested data structures
- `test_custom_patterns()` - Tests custom sanitization patterns

**Coverage:**
- Pattern matching for sensitive data
- Replacement with masked values
- Nested object sanitization
- Custom pattern registration
