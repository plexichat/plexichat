# Utility modules for the Plexichat application.
# Import from common-utils subpackage using importlib to handle hyphenated directory name
import importlib
import sys

# Dynamically load modules from common-utils (which has a hyphen in the name)
# We need to use importlib.import_module since Python doesn't allow hyphens in identifiers

try:
    _config_module = importlib.import_module("src.utils.common-utils.utils.config")
except ImportError as _e:
    raise ImportError(
        f"Failed to import utils.config: {_e}. Check that src/utils/common-utils/ has the required package structure."
    ) from _e

try:
    _logger_module = importlib.import_module("src.utils.common-utils.utils.logger")
except ImportError as _e:
    raise ImportError(
        f"Failed to import utils.logger: {_e}. Check that src/utils/common-utils/ has the required package structure."
    ) from _e

try:
    _validator_module = importlib.import_module(
        "src.utils.common-utils.utils.validator"
    )
except ImportError as _e:
    raise ImportError(
        f"Failed to import utils.validator: {_e}. Check that src/utils/common-utils/ has the required package structure."
    ) from _e

try:
    _version_module = importlib.import_module("src.utils.common-utils.utils.version")
except ImportError as _e:
    raise ImportError(
        f"Failed to import utils.version: {_e}. Check that src/utils/common-utils/ has the required package structure."
    ) from _e

# Register as proper submodules so `import utils.logger as logger` works
sys.modules["utils.config"] = _config_module
sys.modules["utils.logger"] = _logger_module
sys.modules["utils.validator"] = _validator_module
sys.modules["utils.version"] = _version_module

try:
    _licensing_module = importlib.import_module(
        "src.utils.common-utils.utils.licensing"
    )
except ImportError as _e:
    raise ImportError(
        f"Failed to import utils.licensing: {_e}. Check that src/utils/common-utils/ has the required package structure."
    ) from _e

sys.modules["utils.licensing"] = _licensing_module

# Re-export public symbols from each module
for _name in dir(_config_module):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_config_module, _name)

for _name in dir(_logger_module):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_logger_module, _name)

for _name in dir(_validator_module):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_validator_module, _name)

for _name in dir(_version_module):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_version_module, _name)

for _name in dir(_licensing_module):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_licensing_module, _name)


def get_licensing():
    return _licensing_module
