# Common utilities

Reusable Python utilities shared by the Plexichat server and client.

## Components

| Component | Description |
| --- | --- |
| **config** | YAML/JSON configuration loader with environment variable interpolation and malformed-file handling |
| **logger** | Logging with color output, daily log rotation, and PII sanitization |
| **validator** | Input validation and sanitization against SQL injection and XSS |
| **version** | Semantic version parser and comparator for the `stage.major.minor-build` format |
| **licensing** | License key validation and activation |

## Package structure

```
common_utils/
  utils/
    config/core.py      # ConfigLoader class
    logger/core.py      # DualFileHandler, SanitizingFilter
    validator/core.py   # Validator class
    version/core.py     # Version, VersionStage
    licensing/core.py   # License validation
  tests/                # Unit tests for each component
```

## Adding a new utility

When adding a new utility:

1. Place the implementation in `utils/<name>/core.py`.
2. Create a public API in `utils/<name>/__init__.py`.
3. Keep the utility generic enough for both server and client use.
4. Add unit tests in `tests/<name>/`.
