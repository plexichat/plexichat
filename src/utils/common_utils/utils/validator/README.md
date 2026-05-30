# Validator Utility

A utility for validating and sanitizing text data against common vulnerabilities like SQL injection and XSS.

## Features

- **Pattern Matching**: Checks against a configurable list of regex patterns (SQLi, XSS, etc.).
- **HTML Sanitization**: Automatically escapes HTML tags for safe display in web interfaces.
- **Escaping Support**: Optionally allows dangerous patterns if they are properly escaped.
- **Extensible**: Add custom patterns easily.

## Installation

Ensure this directory is in your Python path.

## Usage

### Basic Usage (No setup required)

You can use the validator immediately with sensible defaults:

```python
import utils.validator as validator

def process_user_input(user_data):
    result = validator.validate(user_data)

    if result.is_valid:
        print("Data is safe:", result.sanitized_value)
        # Process the data...
    else:
        print("Validation failed:", result.error_message)
```

### Custom Setup (Optional)

If you want custom patterns or settings, setup once in your main file:

```python
import utils.validator as validator

# Setup - optional, do this ONCE in your main file
validator.setup(
    blocklist_patterns=[r"bad_word", r"forbidden"],
    escape_char='"',
    allow_escaped=True
)
```

Then use it anywhere:

```python
import utils.validator as validator

result = validator.validate("SELECT * FROM users")
if result.is_valid:
    # Safe to use
    pass
```

**The validator auto-initializes with secure defaults if setup is not called!**

### Legacy Usage (Still Supported)

You can also create Validator instances directly:

```python
from utils.validator import Validator

validator = Validator(
    blocklist_patterns=[r"bad_word"],
    escape_char='"',
    allow_escaped=True
)

result = validator.validate("SELECT * FROM users")
if result.is_valid:
    print("Data is safe:", result.sanitized_value)
else:
    print("Validation failed:", result.error_message)
```

### Escaping Rules

If `allow_escaped` is set to `True`, the validator will allow text containing blocked patterns IF the text is enclosed in the `escape_char` (default `"`). By default, `allow_escaped` is `False` for security.

**IMPORTANT**: If the validator returns valid for escaped content, it is the **responsibility of the consuming application** to handle this data carefully (e.g., unescape it safely or treat it as a string literal) and NOT execute it.

## Configuration Options

| Option               | Description                       | Default                 |
| -------------------- | --------------------------------- | ----------------------- |
| `blocklist_patterns` | List of regex patterns to block   | Common SQL/XSS patterns |
| `escape_char`        | Character used for escaping       | `"`                     |
| `allow_escaped`      | Allow blocked patterns if escaped | `False`                 |
| `auto_sanitize_html` | Automatically escape HTML tags    | `True`                  |

## Default Patterns

- `DROP TABLE`
- `DELETE FROM`
- `SELECT ... FROM`
- `INSERT INTO`
- `<script>`
- `javascript:`
