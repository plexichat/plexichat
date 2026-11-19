import re

from src.core.bounded_lru import BoundedLRU
from src.core.security.hardening import require_identifier

# Regex pattern to match ? placeholders (not inside single or double quotes)
_PLACEHOLDER_PATTERN = re.compile(r"""('(?:''|[^'])*'|"(?:""|[^"])*")|(\?)""")

# Process-local cache for converted queries. It used to be a plain dict with a
# hard stop at 1000 entries: once full it silently stopped caching (so stale
# entries were kept forever) and had no byte bound, letting a flood of large,
# distinct query strings pin arbitrary memory. A bounded LRU keeps the hot
# conversions while capping entries, bytes and staleness. Conversion is pure,
# so eviction is safe -- a miss simply recomputes the identical statement; no
# auth or security decision is cached here.
_QUERY_CONVERSION_CACHE_MAX_ENTRIES = 1000
_QUERY_CONVERSION_CACHE_MAX_BYTES = 4 * 1024 * 1024
_QUERY_CONVERSION_CACHE_TTL_SECONDS = 1800.0
_QUERY_CONVERSION_CACHE = BoundedLRU(
    max_entries=_QUERY_CONVERSION_CACHE_MAX_ENTRIES,
    max_bytes=_QUERY_CONVERSION_CACHE_MAX_BYTES,
    ttl_seconds=_QUERY_CONVERSION_CACHE_TTL_SECONDS,
)


def convert_placeholders(query: str, db_type: str) -> str:
    """
    Convert ? placeholders to engine-specific placeholders.
    For PostgreSQL, converts ? to %s.
    """
    if db_type != "postgres":
        return query

    # Check cache first
    cache_key = f"{db_type}:{query}"
    cached = _QUERY_CONVERSION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # 1. Convert abs(random()) to floor(random() * ...) for PostgreSQL
    if "abs(random())" in query.lower():
        query = re.sub(
            r"abs\(random\(\)\)",
            "floor(random() * 9223372036854775807)::bigint",
            query,
            flags=re.IGNORECASE,
        )

    # 2. Convert INSERT OR IGNORE to INSERT ... ON CONFLICT DO NOTHING
    if "INSERT OR IGNORE" in query.upper():
        query = re.sub(
            r"INSERT OR IGNORE INTO", "INSERT INTO", query, flags=re.IGNORECASE
        )
        # Only append if not already present and it's a simple INSERT statement
        if "ON CONFLICT" not in query.upper() and "RETURNING" not in query.upper():
            query = query.strip()
            if query.endswith(";"):
                query = query[:-1] + " ON CONFLICT DO NOTHING;"
            else:
                query = query + " ON CONFLICT DO NOTHING"

    def replace(match):
        if match.group(1):  # It's a quoted string literal in the SQL
            # Escape literal % inside the SQL string itself for the postgres driver
            content = match.group(1)
            return content.replace("%", "%%")
        elif match.group(2):  # It's a ? placeholder
            return "%s"
        else:  # It's a literal % in the SQL
            return "%%"

    # Pattern to match quoted strings, ? placeholders, OR literal %
    pattern = re.compile(
        r"""('(?:''|[^'])*'|"(?:""|[^"])*")|(\?) | (%)""".replace(" ", ""), re.VERBOSE
    )
    converted_query = pattern.sub(replace, query)

    # Store in the bounded cache (entry, byte and TTL limits are enforced by
    # BoundedLRU; an evicted entry is simply recomputed on the next call).
    _QUERY_CONVERSION_CACHE.set(cache_key, converted_query)

    return converted_query


def unescape_postgres_percent(query: str) -> str:
    """Undo ``convert_placeholders``' ``%%`` escaping for a query with no params.

    ``convert_placeholders`` doubles every literal ``%`` for PostgreSQL, which
    is what a paramstyle-aware driver expects: psycopg parses the statement
    client-side and collapses ``%%`` back to ``%``, and pg8000's
    ``convert_paramstyle`` does the same when parameters are bound.

    A *parameterless* statement takes neither path. pg8000 sends it through
    the simple query protocol (``execute_simple``), which performs no
    paramstyle processing at all, so the escapes would reach the server
    verbatim. With no placeholders to substitute the escaping has no purpose,
    so it is reverted; ``%`` is only ever doubled by the converter, which
    makes this an exact inverse.
    """
    return query.replace("%%", "%")


def sanitize_identifier(identifier: str, db_type: str) -> str:
    """
    Sanitize database identifiers (table/column names).
    Ensures safe identifier naming and applies engine-specific quoting.

    The old local regex accepted names of any length; a 10k-character name
    still produced a valid-looking (but planner-hostile) statement. Delegate
    to the shared rule so the 63-character bound and the single definition of
    "safe identifier" are enforced everywhere.
    """
    require_identifier(identifier, "database identifier")

    if db_type == "postgres":
        return f'"{identifier}"'
    else:
        # SQLite uses backticks
        return f"`{identifier}`"
