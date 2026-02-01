import re
from typing import Any

# Regex pattern to match ? placeholders (not inside single or double quotes)
_PLACEHOLDER_PATTERN = re.compile(r'''('(?:''|[^'])*'|"(?:""|[^"])*")|(\?)''')

# Global cache for converted queries to avoid redundant regex processing
_QUERY_CONVERSION_CACHE = {}

def convert_placeholders(query: str, db_type: str) -> str:
    """
    Convert ? placeholders to engine-specific placeholders.
    For PostgreSQL, converts ? to %s.
    """
    if db_type != "postgres":
        return query

    # Check cache first
    cache_key = f"{db_type}:{query}"
    if cache_key in _QUERY_CONVERSION_CACHE:
        return _QUERY_CONVERSION_CACHE[cache_key]

    original_query = query
    # 1. Convert abs(random()) to floor(random() * ...) for PostgreSQL
    if "abs(random())" in query.lower():
        query = re.sub(r"abs\(random\(\)\)", "floor(random() * 9223372036854775807)::bigint", query, flags=re.IGNORECASE)

    # 2. Convert INSERT OR IGNORE to INSERT ... ON CONFLICT DO NOTHING
    if "INSERT OR IGNORE" in query.upper():
        query = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", query, flags=re.IGNORECASE)
        if "ON CONFLICT DO NOTHING" not in query.upper():
            query = query.strip()
            if query.endswith(";"):
                query = query[:-1] + " ON CONFLICT DO NOTHING;"
            else:
                query = query + " ON CONFLICT DO NOTHING"

    def replace(match):
        if match.group(1):  # It's a quoted string literal in the SQL
            # Escape literal % inside the SQL string itself for psycopg2
            content = match.group(1)
            return content.replace("%", "%%")
        elif match.group(2):  # It's a ? placeholder
            return "%s"
        else:  # It's a literal % in the SQL
            return "%%"

    # Pattern to match quoted strings, ? placeholders, OR literal %
    pattern = re.compile(r'''('(?:''|[^'])*'|"(?:""|[^"])*")|(\?) | (%)'''.replace(" ", ""), re.VERBOSE)
    converted_query = pattern.sub(replace, query)
    
    # Store in cache (limit size to prevent memory growth)
    if len(_QUERY_CONVERSION_CACHE) < 1000:
        _QUERY_CONVERSION_CACHE[cache_key] = converted_query
        
    return converted_query

def sanitize_identifier(identifier: str, db_type: str) -> str:
    """
    Sanitize database identifiers (table/column names).
    Ensures safe identifier naming and applies engine-specific quoting.
    """
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
        raise ValueError(f"Invalid database identifier: {identifier}")
        
    if db_type == "postgres":
        return f'"{identifier}"'
    else:
        # SQLite uses backticks
        return f"`{identifier}`"