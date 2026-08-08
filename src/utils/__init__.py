# Utility modules for the Plexichat application.
import importlib
import sys

# Modules re-exported from the bundled ``common_utils`` package, in the
# order they were historically re-exported (later modules may override
# same-named public symbols such as ``setup``).
_SUBMODULES = ("config", "logger", "validator", "version", "licensing")


def _load(name: str):
    """Import a common_utils submodule via its canonical dotted path."""
    try:
        return importlib.import_module(f"src.utils.common_utils.utils.{name}")
    except ImportError as _e:
        raise ImportError(
            f"Failed to import utils.{name}: {_e}. Check that src/utils/common_utils/ has the required package structure."
        ) from _e


def _alias(name: str, module) -> None:
    """Register ``utils.<name>`` (and ``utils.<name>.core``) aliases.

    The real modules live under ``src.utils.common_utils.utils.<name>``.
    Importing them a second time under the shorter ``utils.<name>`` name
    would create a *second* module instance — and therefore second copies
    of every class/enum defined inside — breaking ``isinstance`` checks,
    ``Enum`` keys in dicts (KeyError), and identity comparisons. Registering
    the aliases in ``sys.modules`` (and on the ``utils`` package itself)
    makes every import path resolve to the exact same module object.
    """
    sys.modules[f"utils.{name}"] = module
    globals()[name] = module
    # Alias the ``.core`` submodule too (``from utils.<name>.core import X``
    # is used across the codebase and must return the same objects as the
    # canonical path).
    try:
        core = importlib.import_module(module.__name__ + ".core")
    except ImportError:
        return
    sys.modules[f"utils.{name}.core"] = core
    try:
        setattr(module, "core", core)
    except AttributeError:
        pass


for _name in _SUBMODULES:
    _alias(_name, _load(_name))

# Re-export public symbols from each module
for _module in (globals()[_n] for _n in _SUBMODULES):
    for _attr in dir(_module):
        if not _attr.startswith("_"):
            globals()[_attr] = getattr(_module, _attr)


def get_licensing():
    return globals()["licensing"]
