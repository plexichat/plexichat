# Config tests

Unit tests for the `ConfigLoader` utility.

## Test file

`test_config.py` covers all `ConfigLoader` functionality.

## Test cases

| Class | What it tests |
| --- | --- |
| `TestBasicConfigLoading` | Creating default YAML/JSON files, loading existing files, handling `.yml` extension |
| `TestGetMethod` | Retrieving values with defaults, nested structures, and various types |
| `TestSetMethod` | Setting values, overwriting, saving to YAML and JSON |
| `TestMalformedConfigHandling` | Crash-on-single, crash-on-many, and ignore modes for malformed YAML/JSON |
| `TestUnsupportedFormat` | Rejection of unsupported extensions (`.txt`, `.xml`, no extension) |
| `TestDirectoryCreation` | Automatic creation of nested directories |
| `TestSecurityConfigInjection` | Protection against YAML code execution, special characters, path traversal, null bytes, large files, and Unicode |
| `TestConfigPersistence` | Consistency across multiple `ConfigLoader` instances |
| `TestMalformedConfigActionEnum` | Enum values and comparison |
| `TestEdgeCases` | None, empty string, zero, false, empty list, and empty dict values |

## Running the tests

```bash
pytest src/utils/common_utils/tests/config/ -v
```
