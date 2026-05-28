"""
Media manager — thin orchestrator that composes mixin classes.

The actual implementations live in sibling ``_*.py`` modules that each
provide a mixin class.  ``MediaManager`` inherits from all of them
(and from ``BaseManager``) so the public API surface stays identical.

All optimisation work is preserved in the mixins:
- Small files (≤8 MiB) read directly into memory, avoiding disk I/O.
- Thumbnails deferred to fire-and-forget background thread.
- Malware scan runs in parallel with compression + metadata.
- Exact-hash duplicate check is inline (fast); pHash similarity is background-only.
- Rate-limit update is fire-and-forget.
"""

import threading
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from src.core.base import BaseManager

# ── mixins ────────────────────────────────────────────────────────────────────
from ._config import (  # noqa: F401  (re-exported for convenience)
    DEFAULT_SIZE_LIMITS,
    DEFAULT_ALLOWED_TYPES,
    DEFAULT_THUMBNAIL_SIZES,
)
from ._validation import _ValidationMixin
from ._storage_setup import _StorageSetupMixin
from ._rate_limit import _RateLimitMixin
from ._upload import _UploadMixin
from ._thumbnails import _ThumbnailsMixin
from ._phash import _PhashMixin
from ._files import _FilesMixin
from ._processing import _ProcessingMixin
from ._signing import _SigningMixin
from ._proxy import _ProxyMixin
from ._scanning import _ScanningMixin

logger = logging.getLogger(__name__)


class MediaManager(
    _ValidationMixin,
    _StorageSetupMixin,
    _RateLimitMixin,
    _UploadMixin,
    _ThumbnailsMixin,
    _PhashMixin,
    _FilesMixin,
    _ProcessingMixin,
    _SigningMixin,
    _ProxyMixin,
    _ScanningMixin,
    BaseManager,
):
    """Core media manager — all logic is provided by mixin parent classes."""

    # ── public attributes set by __init__ ─────────────────────────────────────
    _db: object = None
    _messaging: object = None
    _config: dict = {}
    _lock: Optional[threading.RLock] = None
    _rl_prefix: str = ""
    _storage: object = None
    _db_storage: object = None
    _image_processor: object = None
    _video_processor: object = None
    _url_signer: object = None
    _scanner: object = None
    _proxy: object = None
    _executor: Optional[ThreadPoolExecutor] = None
    _dedup_manager: object = None
    _compression_manager: object = None

    def __init__(self, db, messaging_module=None):
        super().__init__(db)
        self._db = db
        self._messaging = messaging_module
        self._config = self._load_config()
        self._lock = threading.RLock()
        self._rl_prefix = f"media:{uuid.uuid4().hex}"

        # Storage backends
        self._storage = self._init_storage()
        self._db_storage = self._init_db_storage()

        # Processing
        self._image_processor = self._init_image_processor()
        self._video_processor = self._init_video_processor()

        # Security
        self._url_signer = self._init_url_signer()
        self._scanner = self._init_scanner()
        self._proxy = self._init_proxy()

        # Thread pool (10 workers)
        self._executor = ThreadPoolExecutor(max_workers=10)

        # Deduplication (imported here to avoid circular imports)
        from .deduplication import setup as dedup_setup, DeduplicationManager

        dedup_setup(db)
        self._dedup_manager = DeduplicationManager(db)

        # Compression
        try:
            from .compression import CompressionManager

            self._compression_manager = CompressionManager()
        except ImportError:
            self._compression_manager = None

        logger.info("Media module initialized")
