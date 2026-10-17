"""
Static Client module.

Optionally serves the Plexichat web client (Vite build output) directly from
the FastAPI backend, downloading releases from the GitLab Releases API and
verifying them with SHA256.

Public surface (re-exported from :mod:`src.static_client`):

* :class:`StaticClientConfig` - typed configuration
* :func:`get_static_client_config` - read config from the central config store
* :class:`StaticClientManager` - install/swap orchestrator
* :func:`get_static_client_manager` - process-wide singleton accessor
* :func:`install_static_client_middleware` - register the ASGI middleware
* :func:`get_static_client_paths` - path set excluded from auth/rate-limit
* :func:`start_static_client_service` - start the background auto-update task
* :func:`stop_static_client_service` - stop the background auto-update task
* :func:`run_static_client_initial_install` - blocking initial fetch
"""

from .config import StaticClientConfig, get_static_client_config
from .manager import (
    StaticClientManager,
    get_static_client_manager,
    reset_static_client_manager,
)
from .router import (
    StaticClientMiddleware,
    StaticPaths,
    get_static_client_paths,
    install_static_client_middleware,
)
from .service import (
    run_static_client_initial_install,
    start_static_client_service,
    stop_static_client_service,
)

__all__ = [
    "StaticClientConfig",
    "get_static_client_config",
    "StaticClientManager",
    "get_static_client_manager",
    "reset_static_client_manager",
    "StaticClientMiddleware",
    "StaticPaths",
    "get_static_client_paths",
    "install_static_client_middleware",
    "start_static_client_service",
    "stop_static_client_service",
    "run_static_client_initial_install",
]
