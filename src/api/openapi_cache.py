"""Persist the generated OpenAPI schema so restarts do not rebuild it.

FastAPI builds the schema lazily on the first ``GET /openapi.json``. With 300+
routes and large Pydantic models that first request cost 12-24s on a cold boot,
which is slow enough to time out self-test route discovery. The schema only
changes when routes or models change, so it is safe to cache on disk and reload
on the next boot.

The cache key includes the app version and a digest of the registered route
paths, so adding/removing/renaming a route naturally misses the cache instead of
serving a stale document.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import utils.logger as logger


def _cache_path(app: Any) -> Path:
    version = str(getattr(app, "version", "unknown"))
    routes = sorted(str(getattr(route, "path", "")) for route in app.routes)
    digest = hashlib.sha256("\n".join(routes).encode("utf-8")).hexdigest()[:16]
    base = Path(
        os.environ.get(
            "PLEXICHAT_CACHE_DIR",
            str(Path(tempfile.gettempdir()) / "plexichat"),
        )
    )
    return base / f"openapi-{version}-{digest}.json"


def load_cached_openapi(app: Any) -> bool:
    """Populate ``app.openapi_schema`` from disk when a matching cache exists."""
    path = _cache_path(app)
    try:
        if not path.exists():
            logger.debug(f"OpenAPI cache miss (no file): {path}")
            return False
        schema = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(schema, dict) or "paths" not in schema:
            return False
        app.openapi_schema = schema
        logger.info(f"OpenAPI cache hit: {path}")
        return True
    except Exception as e:
        logger.debug(f"OpenAPI cache load failed: {e}")
        return False


def save_cached_openapi(app: Any) -> None:
    """Write the generated schema to disk, atomically, best-effort."""
    schema = getattr(app, "openapi_schema", None)
    if not isinstance(schema, dict):
        return
    path = _cache_path(app)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(schema, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        logger.debug(f"OpenAPI cache save failed: {e}")