"""Constants for artifact routes."""

from __future__ import annotations

import re

VALID_EXPORT_FORMATS = frozenset(
    {"html", "pdf", "md", "odt", "txt", "plexiscribe", "plexiscript", "plexiboard"}
)
EXPORT_MIME_MAP = {
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
    "odt": "application/vnd.oasis.opendocument.text",
    "txt": "text/plain; charset=utf-8",
    "plexiscribe": "application/vnd.plexichat.plexiscribe",
    "plexiscript": "application/vnd.plexichat.plexiscript",
    "plexiboard": "application/vnd.plexichat.plexiboard",
}
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_LEN = 80
MAX_ID_LEN = 64
