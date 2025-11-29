"""
Utility modules for PlexiChat application.

Contains:
- encryption: Cryptographic utilities (Argon2id, AES-256-GCM, Ed25519, Snowflake IDs)
- common-utils: Shared utilities (logger, config, validator, version) - git submodule

Usage:
    import utils.logger as logger
    import utils.config as config
    import utils.validator as validator
    import utils.version as version
"""

import os

# Extend __path__ to include common-utils/utils so that submodule imports work
# This allows `import utils.config` to find common-utils/utils/config
_common_utils_subdir = os.path.join(os.path.dirname(__file__), "common-utils", "utils")
if _common_utils_subdir not in __path__:
    __path__.insert(0, _common_utils_subdir)
