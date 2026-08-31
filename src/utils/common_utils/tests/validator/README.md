# Validator unit tests

This directory contains unit tests for the `Validator` utility class, which
validates input strings against configurable blocklist patterns.

## Test file

`test_validator.py`

## What the tests cover

- **Clean text validation** — plain text, empty strings, `None`, whitespace, and
  numeric strings pass validation.
- **SQL injection prevention** — `SELECT`, `UNION`, `DROP TABLE`, `INSERT INTO`,
  `UPDATE SET`, `DELETE FROM`, and comment-based attacks are blocked. Detection is
  case-insensitive and works across line breaks.
- **XSS prevention** — `<script>` tags, event handlers (`onclick`, `onerror`,
  `onload`, `onmouseover`), `<iframe>`, `<object>`, `javascript:` URIs, and
  `<svg onload>` vectors are blocked.
- **HTML sanitization** — when `auto_sanitize_html=True`, special characters are
  escaped (`<`, `>`, `&`, `"`, `'`). Escaped content is safe for HTML output.
- **Escape character handling** — escaped content bounded by a configurable
  `escape_char` bypasses blocklist checks when `allow_escaped=True`.
- **Custom blocklist patterns** — arbitrary regex patterns can be added at
  construction or via `add_pattern()`.
- **Edge cases** — very long strings, Unicode characters, null bytes, mixed attack
  vectors, and anchor-based patterns (`^`, `$`).

## Key classes

| Class | Description |
|-------|-------------|
| `TestValidationResult` | `ValidationResult` dataclass field access |
| `TestBasicValidation` | Plain-text input that should pass |
| `TestSQLInjectionPrevention` | SQL injection attack vectors |
| `TestXSSPrevention` | Cross-site scripting attack vectors |
| `TestHTMLSanitization` | HTML entity escaping |
| `TestEscapeCharHandling` | Escape-char boundary behavior |
| `TestCustomPatterns` | User-supplied regex patterns |
| `TestAdvancedSQLInjection` | Stacked queries, blind injection, hex encoding |
| `TestAdvancedXSS` | Encoded payloads, SVG, data URIs |
| `TestConfigurationOptions` | Constructor defaults and compiled pattern counts |
| `TestEdgeCases` | Boundary conditions and mixed vectors |

## Related source code

- `src/utils/common_utils/validator.py` — `Validator` and `ValidationResult`
  classes
