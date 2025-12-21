# Property-Based Testing with Hypothesis

This directory contains comprehensive property-based tests using [Hypothesis](https://hypothesis.readthedocs.io/), a Python library for automatic test case generation.

## Overview

Property-based testing automatically generates hundreds of test cases to validate that your code works correctly across a wide range of inputs, including edge cases you might not think to test manually.

## Test Files

### `test_property_based_validation.py`
Core validation tests for all managers:
- **AuthManager**: Username, email, password validation
- **MessagingManager**: Message content, length limits, Unicode handling
- **ServersManager**: Server names, channel names, role names
- **WebhookManager**: Webhook names, URLs, avatar validation
- **JSON/Metadata**: JSON parsing, malformed data
- **Unicode**: Emoji, RTL text, CJK characters, control characters
- **Boundaries**: Length limits, count limits
- **Security**: XSS, SQL injection patterns

### `test_property_based_managers.py`
Manager-specific advanced tests:
- **Permission Systems**: Permission dictionaries, overrides
- **Snowflake IDs**: ID generation, ordering
- **Rate Limiting**: Bucket tracking, reset times
- **Pagination**: Cursor-based, offset-based
- **Invites**: Code generation, expiry, max uses
- **Timestamps**: Millisecond precision, comparisons
- **Attachments**: Size limits, count limits
- **Embeds**: Field limits, color validation

### `test_property_based_edge_cases.py`
Extreme edge cases and security tests:
- **Zero-Width Characters**: Invisible Unicode characters
- **Homograph Attacks**: Visual spoofing (Cyrillic/Latin)
- **Bidirectional Text**: RTL/LTR mixing
- **Combining Characters**: Zalgo text, excessive diacritics
- **Unicode Normalization**: NFC vs NFD forms
- **SQL Injection**: Various injection patterns
- **XSS Prevention**: Script tags, event handlers
- **Path Traversal**: Directory traversal attempts
- **Buffer Overflows**: Very long strings
- **Null Bytes**: Special character handling
- **Integer Overflow**: Large/negative integers
- **Concurrent Access**: Simulated race conditions

## Running Tests

### Run all property-based tests:
```bash
pytest src/tests/unit/test_property_based*.py -v
```

### Run specific test file:
```bash
pytest src/tests/unit/test_property_based_validation.py -v
```

### Run with more examples (thorough testing):
```bash
pytest src/tests/unit/test_property_based*.py -v --hypothesis-show-statistics --hypothesis-profile=thorough
```

### Run with minimal examples (quick check):
```bash
pytest src/tests/unit/test_property_based*.py -v --hypothesis-profile=quick
```

### Run specific test class:
```bash
pytest src/tests/unit/test_property_based_validation.py::TestAuthManagerPropertyBased -v
```

## Configuration

Hypothesis settings can be configured in `pytest.ini` or `pyproject.toml`:

```ini
[tool:pytest]
hypothesis_profile = default

[hypothesis]
derandomize = true
max_examples = 200
deadline = None
```

## Common Patterns

### Custom Strategies

Strategies generate test data. Examples:

```python
from hypothesis import strategies as st

# Generate valid usernames
usernames = st.text(
    min_size=3, 
    max_size=32,
    alphabet=st.characters(whitelist_categories=('Ll', 'Lu', 'Nd'), whitelist_characters='_')
)

# Generate email addresses
emails = st.builds(
    lambda user, domain: f"{user}@{domain}.com",
    st.text(min_size=1, max_size=20, alphabet=st.characters(min_codepoint=97, max_codepoint=122)),
    st.sampled_from(['gmail', 'yahoo', 'example'])
)
```

### Property Tests

Tests define properties that should always hold:

```python
@given(st.text(min_size=0, max_size=4000))
@settings(max_examples=200)
def test_message_validation(content):
    """Empty messages should be rejected."""
    result = validate_content(content, max_length=4000)
    
    if not content.strip():
        assert not result.valid
```

### Example Decorator

Add specific examples to test alongside generated ones:

```python
@given(usernames())
@example("validuser123")
@example("Test_User")
def test_username_validation(username):
    valid, issues = validate_username(username)
    assert isinstance(valid, bool)
```

## Test Coverage Areas

### Input Validation
- ✅ Length limits (min/max)
- ✅ Character sets (alphanumeric, special)
- ✅ Format validation (email, URLs)
- ✅ Empty/whitespace-only inputs
- ✅ Unicode edge cases

### Security
- ✅ SQL injection patterns
- ✅ XSS attempts (script tags, event handlers)
- ✅ Path traversal attempts
- ✅ Homograph attacks (visual spoofing)
- ✅ Zero-width character abuse

### Boundary Conditions
- ✅ Zero values
- ✅ Maximum values (2^63-1 for IDs)
- ✅ Just under/over limits
- ✅ Very large inputs (10K+ chars)

### Unicode & Internationalization
- ✅ Emoji (U+1F600-1F64F)
- ✅ RTL text (Hebrew, Arabic)
- ✅ CJK characters (Chinese/Japanese/Korean)
- ✅ Combining diacriticals
- ✅ Control characters
- ✅ Normalization (NFC vs NFD)

### Data Integrity
- ✅ JSON parsing (valid/malformed)
- ✅ Metadata serialization
- ✅ Timestamp calculations
- ✅ ID generation/ordering

## Debugging Failed Tests

When Hypothesis finds a failing case:

### 1. Check the Minimal Example
Hypothesis automatically simplifies failing cases:

```
Falsifying example: test_username_validation(username='a\x00b')
```

### 2. Use `@reproduce_failure` Decorator
Add to reproduce the exact failure:

```python
from hypothesis import reproduce_failure

@reproduce_failure('6.92.1', b'AXByAGI=')
@given(st.text())
def test_something(text):
    ...
```

### 3. Add Logging
```python
@given(st.text())
def test_something(text):
    print(f"Testing with: {repr(text)}")
    ...
```

## Best Practices

### DO:
- ✅ Use `assume()` to filter invalid inputs for test focus
- ✅ Test invariants (properties that always hold)
- ✅ Use `@example()` for known edge cases
- ✅ Set reasonable `max_examples` (100-500)
- ✅ Handle expected exceptions gracefully

### DON'T:
- ❌ Test exact output values (test properties instead)
- ❌ Use random.random() (non-deterministic)
- ❌ Ignore Unicode edge cases
- ❌ Forget to test empty/null cases
- ❌ Skip boundary value testing

## Performance Considerations

Property-based tests run many examples, so:

1. **Use `deadline=None`** for slow operations
2. **Reduce `max_examples`** during development
3. **Use `@settings(max_examples=50)`** for slow tests
4. **Profile with** `--hypothesis-show-statistics`

## Integration with CI/CD

In CI pipelines:

```yaml
# .gitlab-ci.yml
test:
  script:
    - pip install -r requirements-test.txt
    - pytest src/tests/unit/test_property_based*.py -v --hypothesis-profile=ci
```

Define CI profile in `pytest.ini`:

```ini
[hypothesis.profiles.ci]
max_examples = 500
deadline = 5000
derandomize = true
```

## Further Reading

- [Hypothesis Documentation](https://hypothesis.readthedocs.io/)
- [Property-Based Testing Guide](https://hypothesis.works/articles/what-is-property-based-testing/)
- [Hypothesis Strategies](https://hypothesis.readthedocs.io/en/latest/data.html)
- [Testing Unicode](https://hypothesis.readthedocs.io/en/latest/data.html#hypothesis.strategies.characters)

## Maintenance

When adding new validators or managers:

1. Add property tests to appropriate file
2. Define custom strategies if needed
3. Test both valid and invalid inputs
4. Include Unicode and security edge cases
5. Add examples for known edge cases
6. Update this README with coverage info
