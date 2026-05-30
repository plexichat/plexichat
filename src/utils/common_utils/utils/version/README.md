# Version Utility

A lightweight version parsing and comparison utility for applications using a stage-based versioning scheme.

## Version Format

```
[stage].[major].[minor]-[build]
```

| Component | Values | Description |
|-----------|--------|-------------|
| stage | `a`, `b`, `c`, `r` | Alpha, Beta, Candidate, Release |
| major | 1+ | Major version (breaking changes) |
| minor | 0+ | Minor version (new features) |
| build | 1+ | Build number (resets on minor bump) |

### Examples

- `a.1.0-1` - Alpha, first major version, first build
- `b.2.3-15` - Beta, second major, third minor, 15th build
- `r.1.0-1` - Release version 1.0, first build

### Version Ordering

Versions are compared in order: stage > major > minor > build

Stage ordering (lowest to highest):
1. `a` (Alpha)
2. `b` (Beta)
3. `c` (Candidate)
4. `r` (Release)

Example comparisons:
- `a.1.0-1` < `b.1.0-1` (beta > alpha)
- `r.1.0-1` < `r.2.0-1` (major 2 > major 1)
- `r.1.1-1` < `r.1.2-1` (minor 2 > minor 1)
- `r.1.0-1` < `r.1.0-2` (build 2 > build 1)

## Setup

```python
import utils.version as version

# Configure once in main.py
version.setup(
    current_version="a.1.0-1",
    min_supported_version="a.1.0-1"  # Optional: minimum client version
)
```

## Usage

```python
import utils.version as version

# Get current version
ver = version.current()
ver_str = version.current_string()  # "a.1.0-1"

# Parse a version string
parsed = version.parse("b.2.3-15")
print(parsed.stage)  # VersionStage.BETA
print(parsed.major)  # 2
print(parsed.minor)  # 3
print(parsed.build)  # 15

# Compare versions
result = version.compare("r.1.0-1", "a.1.0-1")
# Returns: 1 (r.1.0-1 is greater)

# Check client compatibility
is_ok = version.is_client_compatible("a.1.0-1")

# Convert to dictionary (useful for API responses)
ver_dict = version.to_dict(parsed)
# {"stage": "b", "major": 2, "minor": 3, "build": 15, "string": "b.2.3-15"}
```

## Advanced Usage

```python
from utils.version import (
    parse_version,
    compare_versions,
    increment_build,
    increment_minor,
    increment_major,
    change_stage,
    VersionStage,
)

# Parse and manipulate versions
ver = parse_version("a.1.0-1")

# Increment build: a.1.0-1 -> a.1.0-2
new_ver = increment_build(ver)

# Increment minor (build resets): a.1.0-1 -> a.1.1-1
new_ver = increment_minor(ver)

# Increment major (minor and build reset): a.1.0-1 -> a.2.0-1
new_ver = increment_major(ver)

# Change stage (build resets): a.1.0-1 -> b.1.0-1
new_ver = change_stage(ver, VersionStage.BETA)

# Check if versions are on same release line
from utils.version.core import is_same_release_line
is_same = is_same_release_line("r.1.0-1", "r.1.5-10")  # True (same stage + major)
```

## Error Handling

```python
from utils.version import parse, InvalidVersionError

try:
    ver = parse("invalid")
except InvalidVersionError as e:
    print(f"Bad version: {e}")

# Common validation errors:
# - Empty string
# - Invalid stage character
# - Major version < 1
# - Minor version < 0
# - Build number < 1
# - Wrong format (missing components, wrong separators)
```

## API Reference

### Module Functions

| Function | Description |
|----------|-------------|
| `setup(current_version, min_supported_version=None)` | Initialize the version utility |
| `current()` | Get current Version object |
| `current_string()` | Get current version as string |
| `min_supported()` | Get minimum supported Version |
| `parse(version_string)` | Parse string to Version |
| `compare(v1, v2)` | Compare two version strings (-1, 0, 1) |
| `is_client_compatible(client_version)` | Check if client meets minimum |
| `to_dict(version)` | Convert Version to dictionary |

### Core Functions

| Function | Description |
|----------|-------------|
| `parse_version(string)` | Parse version string |
| `format_version(version)` | Format Version as string |
| `compare_versions(v1, v2)` | Compare version strings |
| `compare_version_objects(v1, v2)` | Compare Version objects |
| `is_compatible(client, min)` | Check version compatibility |
| `is_same_release_line(v1, v2)` | Check same stage + major |
| `increment_build(version)` | Increment build number |
| `increment_minor(version)` | Increment minor (reset build) |
| `increment_major(version)` | Increment major (reset minor, build) |
| `change_stage(version, stage)` | Change stage (reset build) |

### Classes

| Class | Description |
|-------|-------------|
| `Version` | Immutable version data class |
| `VersionStage` | Enum: ALPHA, BETA, CANDIDATE, RELEASE |
| `InvalidVersionError` | Exception for invalid versions |
