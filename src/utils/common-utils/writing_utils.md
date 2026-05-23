# Writing New Utilities

This document outlines the guidelines for adding new utilities to the `common-utils` repository.

## Directory Structure

Each new utility should have its own directory under `utils/` and a corresponding test directory under `tests/`.

```
utils/
  [util_name]/
    __init__.py
    core.py
    README.md
tests/
  [util_name]/
    test_[util_name].py
```

## Guidelines

1.  **Independence**: Utilities should be as standalone as possible. NO tight coupling between utilities.
2.  **Configuration**: Utilities should be configurable. The "Main File" of the consuming application should be responsible for providing this configuration.
3.  **No Placeholders**: Code should be production-ready. No `TODO`s or placeholder code that requires the user to fill in blanks.
4.  **Unicode**: Avoid using unicode characters in code or comments unless absolutely necessary.
5.  **Testing**: Every utility MUST have comprehensive unit tests.
    - Tests should use the `temp/` directory for any file I/O.
    - Tests should cover all features and edge cases.
6.  **Documentation**: Each utility must have its own `README.md` explaining:
    - Purpose
    - Installation/Setup
    - Usage examples
    - Configuration options
7.  **Error Handling**: Define clear failure conditions. Allow the consuming application to decide how to handle errors (e.g., crash vs. ignore) where appropriate.

## Adding a New Tool

1.  Create the directory structure.
2.  Implement the core logic in `utils/[util_name]/core.py`.
3.  Expose the main classes/functions in `utils/[util_name]/__init__.py`.
4.  Write tests in `tests/[util_name]/`.
5.  Write the `README.md`.
6.  Update the root `README.md` to include the new tool.
