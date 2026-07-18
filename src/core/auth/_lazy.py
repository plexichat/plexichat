"""
Lazy accessors to break circular imports.

Several auth submodules need the AuthManager class only at call time
(via AuthManager.get_instance()). Importing it at module top-level would
create an import cycle:

    manager -> managers.base -> <mixins> -> manager

So the class is resolved lazily on first use.
"""


def _get_auth_manager():
    """Return the AuthManager class, importing it lazily."""
    from .manager import AuthManager

    return AuthManager
