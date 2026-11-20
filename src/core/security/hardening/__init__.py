"""Defense-in-depth helpers, grouped by the value they guard.

Each submodule answers one question that previously got a slightly different
answer at every call site:

``headers`` / ``filenames``
    What may appear in an HTTP header (Content-Disposition, filename parts).
``identifiers``
    What may name a SQL object, for statements that cannot take placeholders.
``paths``
    What may be joined onto a filesystem base.
``numbers``
    What an integer from configuration or a query string may be.
``text``
    What may be copied into a header or log line as-is.

Import from this package rather than from a submodule, so the surface stays one
documented list. Submodules exist so a reader can find the rule by the kind of
value they hold, not by remembering a single grab-bag module name.
"""

from .errors import HardeningViolation
from .filenames import content_disposition, sanitize_header_filename
from .identifiers import is_identifier, require_identifier
from .numbers import SNOWFLAKE_MAX, bounded_int, clamp_finite_float, parse_id
from .paths import safe_join
from .text import reject_control_characters
from .urls import require_http_url, require_https_url, require_ws_url

__all__ = [
    "HardeningViolation",
    "SNOWFLAKE_MAX",
    "bounded_int",
    "clamp_finite_float",
    "content_disposition",
    "is_identifier",
    "parse_id",
    "reject_control_characters",
    "require_http_url",
    "require_https_url",
    "require_ws_url",
    "require_identifier",
    "safe_join",
    "sanitize_header_filename",
]
