# Version unit tests

This directory contains unit tests for the `Version` utility, which handles
semantic-version-like parsing, formatting, comparison, and increment operations.

## Test file

`test_version.py`

## What the tests cover

- **Version parsing** — `a.M.m-B` strings (alpha, beta, candidate, release) are
  parsed into `Version` objects. Whitespace, case, and large numbers are handled.
- **Invalid version handling** — empty strings, `None`, invalid stages, missing
  components, negative values, semver strings, and special characters raise
  `InvalidVersionError`.
- **Formatting and roundtrip** — `format_version` produces the canonical string
  form; `parse → format` roundtrips preserve the original string for all stages.
- **Comparison** — versions are ordered by stage, then major, minor, and build.
  The `compare_versions` and `compare_version_objects` functions return `-1`, `0`,
  or `1`.
- **Compatibility** — `is_compatible(client, requirement)` returns `True` when the
  client version satisfies the requirement (same or higher stage and version).
- **Release line detection** — `is_same_release_line` returns `True` when two
  versions share the same stage and major number.
- **Increment operations** — `increment_build`, `increment_minor`,
  `increment_major`, and `change_stage` produce correctly ordered versions.
- **Dataclass constraints** — `Version` instances are frozen (immutable), hashable,
  and enforce minimum-value invariants (major ≥ 1, build ≥ 1).
- **Security tests** — SQL injection, XSS, command injection, path traversal,
  null bytes, Unicode normalization, and format-string attacks in version strings
  all raise `InvalidVersionError`.

## Key classes

| Class | Description |
|-------|-------------|
| `TestVersionStage` | `VersionStage` enum values and ordering |
| `TestVersionParsing` | Valid version string parsing |
| `TestVersionParsingErrors` | Rejection of malformed input |
| `TestVersionFormatting` | Canonical string output |
| `TestVersionComparison` | Ordering semantics |
| `TestVersionCompatibility` | Client/requirement compatibility |
| `TestSameReleaseLine` | Release-line matching |
| `TestVersionIncrement` | Build, minor, major, and stage increments |
| `TestVersionDataclass` | Immutability, equality, and hashing |
| `TestVersionConstraints` | Constructor value invariants |
| `TestVersionPattern` | `VERSION_PATTERN` regex matching |
| `TestSecurityVersionManipulation` | Injection attack rejection |
| `TestVersionComparisonsEdgeCases` | Dominance ordering edge cases |
| `TestComplexScenarios` | Upgrade paths, sorting, and migration chains |
| `TestErrorMessages` | Error message quality |

## Related source code

- `src/utils/common_utils/version.py` — `Version`, `VersionStage`,
  `InvalidVersionError`, and public API
- `src/utils/common_utils/version/core.py` — `compare_version_objects`,
  `is_same_release_line`, `increment_*`, `change_stage`, `STAGE_ORDER`, and
  `VERSION_PATTERN`
