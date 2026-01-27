import re
from typing import Any

# Regex pattern to match ? placeholders (not inside single or double quotes)
# Corrected with escaped double quotes for safe string representation
_PLACEHOLDER_PATTERN = re.compile(r"('(?:''|[^'])*'|"(?:""|[^\"])*")|(\?)")

def convert_placeholders(query: str, db_type: str) -> str:
    """
    Convert ? placeholders to engine-specific placeholders.
    For PostgreSQL, converts ? to %s.
    """
    if db_type != "postgres":
        return query

    # Handle Postgres-specific escaping and syntax
    # 1. Escape literal % for psycopg2 (must be %%)
    query = query.replace("%", "%%")

    # 2. Convert INSERT OR IGNORE to INSERT ... ON CONFLICT DO NOTHING
    if "INSERT OR IGNORE" in query.upper():
        query = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", query, flags=re.IGNORECASE)
        if "ON CONFLICT DO NOTHING" not in query.upper():
            query = query.strip()
            if query.endswith(";"):
                query = query[:-1] + " ON CONFLICT DO NOTHING;"
            else:
                query = query + " ON CONFLICT DO NOTHING"
    
    # 3. Convert abs(random()) to floor(random() * ...) for PostgreSQL
    if "abs(random())" in query.lower():
        query = re.sub(r"abs\(random\(\)", "floor(random() * 9223372036854775807)::bigint", query, flags=re.IGNORECASE)

    def replace(match):
        if match.group(1):  # It's a quoted string
            return match.group(1)
        else:  # It's a ? placeholder
            return "%s"

    return _PLACEHOLDER_PATTERN.sub(replace, query)

def sanitize_identifier(identifier: str, db_type: str) -> str:
    """
    Sanitize database identifiers (table/column names).
    Ensures safe identifier naming and applies engine-specific quoting.
    """
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", identifier):
        raise ValueError(f"Invalid database identifier: {identifier}")
        
    if db_type == "postgres":
        return f'\"{identifier}\"'  # Corrected escaping for double quotes within f-string
    else:
        # SQLite uses backticks
        return f'`{identifier}`'