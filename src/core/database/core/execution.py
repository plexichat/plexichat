import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Protocol, Tuple

import utils.logger as logger

from src.core.security.hardening import bounded_int

from .. import dialect
from .metrics import _query_count, _query_time_ms
from .statements import get_prepared_statement
from .types import DbConnection, DbCursor

_QUERY_CACHE_MAX = 100

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999. Leave headroom so a
# batched IN(...) lookup can never trip that limit even when the statement
# carries a few extra bound parameters from a surrounding query.
_MAX_BATCH_PARAMS = 900

_DDL_STATEMENT_PREFIXES = ("CREATE", "ALTER")

# Splits a SQL string into alternating code / single-quoted-literal segments
# ('' inside a literal is an escaped quote). Substitutions are applied ONLY to
# the code segments so string-literal data is never rewritten.
_SQL_LITERAL_SPLIT_RE = re.compile(r"('(?:[^']|'')*')")

# Quoted literals, quoted identifiers, dollar-quoted bodies and comments cannot
# carry a real statement separator. Blank them before counting ``;`` so a
# semicolon inside e.g. ``'a; b'`` does not look like a second statement.
# Dollar-quoted bodies (``$$...$$`` / ``$tag$...$tag$``) MUST be blanked as a
# unit too: left un-neutralised, a lone quote inside one pairs with a quote
# outside it and swallows a genuine separator (e.g. ``$$'$$; UPDATE ...;
# $$'$$``), making a stacked write look like a single read. Anything else this
# pattern fails to neutralise (nested block comments, ...) can only add a
# spurious separator -- i.e. over-invalidate the short-lived cache -- which is
# safe. Missing a real separator is not: see ``_has_multiple_statements``.
#
# NOTE: ``#`` is deliberately NOT treated as a comment here. Neither supported
# dialect uses it as one (both use ``--`` and ``/* ... */``); on PostgreSQL it is
# the integer XOR operator, and parameterless statements go through pg8000's
# simple-query protocol, which executes EVERY ``;``-separated statement in the
# string. Neutralising ``#[^\n]*`` would hide a stacked ``; UPDATE ...`` behind
# an XOR expression (e.g. ``SELECT 1 # 1; UPDATE authz_roles ...``) from
# ``_has_multiple_statements``, leaving the query cache uncleared after a write.
# SQLite's ``#`` tails are stripped by ``_sanitize_sqlite_query`` before
# execution, so classifying them as writes here can only over-invalidate.
_SQL_NON_CODE_RE = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\""
    r"|\$\$.*?\$\$|\$(?P<dollar_tag>[A-Za-z_][A-Za-z_0-9]*)\$.*?\$(?P=dollar_tag)\$"
    r"|--[^\n]*|/\*.*?\*/",
    re.DOTALL,
)

# Statement types that are guaranteed not to mutate table data. Everything
# else is treated as a write by the query-result-cache invalidation check:
# fail closed. ``WITH`` is deliberately absent -- a leading CTE can wrap
# INSERT/UPDATE/DELETE (``WITH ... UPDATE ...``). ``EXPLAIN`` is absent too:
# bare ``EXPLAIN`` only prints a plan, but PostgreSQL's ``EXPLAIN ANALYZE``
# (also ``EXPLAIN (ANALYZE, ...)``) EXECUTES its target statement, so
# ``EXPLAIN ANALYZE UPDATE ...`` commits a write that the prefix check would
# otherwise call a read -- leaving cached SELECTs over the mutated table stale
# (a cached authorization verdict surviving a role demotion, for example).
# Over-invalidating the short-lived query cache for rare ``WITH ... SELECT`` /
# ``EXPLAIN SELECT`` readers is harmless; serving a pre-write snapshot is not.
_QUERY_CACHE_READ_PREFIXES = (
    "SELECT",
    "PRAGMA",
    "VALUES",
    "SHOW",
    "DESCRIBE",
    "DESC",
)


def _strip_leading_sql_comments(sql: str) -> str:
    """Return ``sql`` with its leading whitespace and SQL comments removed.

    The preparation pipeline accepts ``--`` line comments, ``/* ... */``
    block comments, and (on SQLite) ``#`` line comments. A write hidden behind
    any of them still executes, so the invalidation check must see past them.
    Only the leading run is stripped; comments inside the statement are left
    exactly as they were.
    """
    rest = sql.lstrip()
    while True:
        if rest.startswith("--") or rest.startswith("#"):
            newline = rest.find("\n")
            if newline < 0:
                return ""
            rest = rest[newline + 1 :].lstrip()
        elif rest.startswith("/*"):
            end = rest.find("*/", 2)
            if end < 0:
                return ""
            rest = rest[end + 2 :].lstrip()
        else:
            return rest


def _has_multiple_statements(query: str) -> bool:
    """True when ``query`` carries more than one SQL statement.

    pg8000 sends parameterless statements through PostgreSQL's simple-query
    protocol, which executes every ``;``-separated statement in the string. A
    prefix-only read/write check sees just the first statement, so a write
    stacked behind a harmless-looking ``SELECT ...;`` would mutate the table
    without clearing the cache -- leaving a cached authorization verdict
    serving the pre-write role. Fail closed: any top-level separator makes the
    whole string a write (over-invalidating a multi-SELECT batch is harmless).
    """
    neutralized = _SQL_NON_CODE_RE.sub(" ", query)
    return sum(1 for part in neutralized.split(";") if part.strip()) > 1


def _query_mutates_data(query: str) -> bool:
    """True when ``query`` may mutate table data and must clear the query cache.

    Fails closed: only a single, known read-only statement is exempt. This
    catches writes the old ``startswith(("INSERT", "UPDATE", ...))`` check
    missed -- CTE writes, ``MERGE``, ``TRUNCATE``, DML preceded by comments that
    the driver strips before execution, and writes stacked behind a read in one
    string (``SELECT 1; UPDATE ...``) that the simple-query protocol runs.
    """
    effective = _strip_leading_sql_comments(query)
    if not effective:
        return False
    if _has_multiple_statements(effective):
        return True
    return not effective.upper().startswith(_QUERY_CACHE_READ_PREFIXES)


def _convert_ddl_fragment(fragment: str) -> str:
    """Apply the PostgreSQL type rewrites to one literal-free SQL fragment."""
    converted = re.sub(r"\bINTEGER\b", "BIGINT", fragment, flags=re.IGNORECASE)
    converted = re.sub(
        r"\bBOOLEAN\s+DEFAULT\s+0\b",
        "BOOLEAN DEFAULT FALSE",
        converted,
        flags=re.IGNORECASE,
    )
    converted = re.sub(
        r"\bBOOLEAN\s+DEFAULT\s+1\b",
        "BOOLEAN DEFAULT TRUE",
        converted,
        flags=re.IGNORECASE,
    )
    return converted


def _is_ddl_statement(query: str) -> bool:
    """True only for CREATE/ALTER statements (the DDL migration surface)."""
    stripped = query.lstrip()
    upper = stripped[:7].upper()
    return upper.startswith(_DDL_STATEMENT_PREFIXES)


def apply_pg_type_fixes(query: str) -> str:
    """Auto-fix INTEGER→BIGINT and other PG16 type issues in DDL statements.

    Migration files run raw SQL through ``db.execute()`` which bypasses the
    ``convert_schema`` pipeline used by schema files.  This function ensures
    INTEGER columns that may hold 64-bit snowflake IDs are upgraded to BIGINT,
    and fixes other PostgreSQL‑16 strict‑typing incompatibilities.

    The rewrite is restricted to DDL statements (CREATE/ALTER) AND to text
    outside single-quoted string literals, so generic DML and any literal
    data containing e.g. the word "INTEGER" are passed through untouched.
    """
    if not _is_ddl_statement(query):
        return query

    segments = _SQL_LITERAL_SPLIT_RE.split(query)
    return "".join(
        segment if index % 2 == 1 else _convert_ddl_fragment(segment)
        for index, segment in enumerate(segments)
    )


class _DatabaseExecutionProtocol(Protocol):
    _query_cache: "OrderedDict[str, Tuple[float, Any]]"
    _query_cache_ttl: float
    _query_cache_lock: Any
    _statement_cache: Any
    _pool: Any
    _local: Any
    engine: Any
    monitor: Any
    type: str
    _slow_query_threshold_ms: float

    @property
    def transaction_depth(self) -> int: ...

    @transaction_depth.setter
    def transaction_depth(self, value: int) -> None: ...

    @property
    def in_transaction(self) -> bool: ...

    @in_transaction.setter
    def in_transaction(self, value: bool) -> None: ...

    def _get_conn(self, auto_connect: bool = True) -> DbConnection: ...

    def _validate_transaction_state(self) -> None: ...

    def _handle_execution_error(
        self, conn: Any, cursor: Any, e: BaseException, query: str
    ) -> None: ...

    def _commit_strict(self, conn: Any, context: str) -> None: ...

    def _mark_pool_in_flight(self, conn: Any, in_flight: bool) -> None: ...

    def _release_conn(self) -> None: ...

    def execute(
        self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> DbCursor: ...

    def execute_many(
        self,
        query: str,
        params_list: List[Tuple],
        auto_commit: bool = True,
    ) -> DbCursor: ...

    def fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]: ...

    def fetch_many_by_ids(
        self,
        table: str,
        ids: Any,
        key_column: str = "id",
        columns: Optional[List[str]] = None,
        batch_size: int = _MAX_BATCH_PARAMS,
    ) -> Dict[Any, Dict[str, Any]]: ...

    def insert_or_ignore(
        self, table: str, columns: List[str], values: Tuple
    ) -> bool: ...

    def _sanitize_sqlite_query(self, query: str) -> str: ...

    def _compile_query(self, query: str, has_params: bool) -> str: ...

    def _prepare_query(self, query: str, has_params: bool) -> str: ...

    def fetch_all(
        self, query: str, params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]: ...

    def _is_connection_error(self, e: BaseException) -> bool: ...

    def _is_schema_error(self, e: BaseException) -> bool: ...

    def _cache_get(self, key: str) -> Optional[Any]: ...

    def _cache_put(self, key: str, value: Any, ttl: float) -> None: ...


class DatabaseExecutionMixin:
    def _sanitize_sqlite_query(self: _DatabaseExecutionProtocol, query: str) -> str:
        if self.type != "sqlite" or "#" not in query:
            return query

        cleaned_lines: List[str] = []
        for line in query.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue

            # Strip inline "# comment" segments (SQLite doesn't support this comment style).
            in_single = False
            in_double = False
            escaped = False
            out_chars: List[str] = []

            for ch in line:
                if escaped:
                    out_chars.append(ch)
                    escaped = False
                    continue

                if ch == "\\":
                    out_chars.append(ch)
                    escaped = True
                    continue

                if ch == "'" and not in_double:
                    in_single = not in_single
                    out_chars.append(ch)
                    continue

                if ch == '"' and not in_single:
                    in_double = not in_double
                    out_chars.append(ch)
                    continue

                if ch == "#" and not in_single and not in_double:
                    break

                out_chars.append(ch)

            sanitized_line = "".join(out_chars).rstrip()
            if sanitized_line:
                cleaned_lines.append(sanitized_line)

        return "\n".join(cleaned_lines)

    def _commit_strict(
        self: _DatabaseExecutionProtocol, conn: Any, context: str
    ) -> None:
        """Commit and NEVER swallow failure.

        A failed commit means the write was not persisted (classic case:
        PostgreSQL connection drop between statement success and COMMIT).
        Silently discarding that error makes callers believe lost writes
        succeeded. Retry once on the same connection (safe: a failed commit
        persisted nothing), then log ERROR and re-raise.
        """
        try:
            conn.commit()
            return
        except Exception as first_error:
            logger.error(
                f"Commit failed ({type(first_error).__name__}) during {context}: "
                f"{first_error}; retrying once"
            )

        try:
            time.sleep(0.05)
            conn.commit()
            logger.warning(f"Commit succeeded on retry during {context}")
        except Exception as e:
            logger.error(
                f"Commit FAILED permanently during {context}: {e} — "
                "the write was NOT persisted"
            )
            self.monitor.record_error(type(e).__name__)
            raise

    def _mark_pool_in_flight(
        self: _DatabaseExecutionProtocol, conn: Any, in_flight: bool
    ) -> None:
        """Tell the connection pool (SQLite) whether ``conn`` has a cursor
        mid-statement so the idle reaper never closes it underneath us."""
        marker = getattr(self._pool, "mark_in_flight", None)
        if callable(marker):
            try:
                marker(conn, in_flight)
            except Exception as mark_error:
                logger.debug(f"in-flight connection marker failed: {mark_error}")

    def _compile_query(
        self: _DatabaseExecutionProtocol, query: str, has_params: bool
    ) -> str:
        """Run the full preparation pipeline once for a novel SQL string."""
        query_sanitized = self._sanitize_sqlite_query(query)
        query_conv = dialect.convert_placeholders(query_sanitized, self.type)

        if self.type == "postgres":
            query_conv = apply_pg_type_fixes(query_conv)
            if not has_params:
                # A parameterless statement bypasses the driver's paramstyle
                # handling entirely, so revert the ``%%`` escaping that only
                # exists for the parameterised path (see the dialect helper).
                query_conv = dialect.unescape_postgres_percent(query_conv)
        return query_conv

    def _prepare_query(
        self: _DatabaseExecutionProtocol, query: str, has_params: bool
    ) -> str:
        """Return prepared SQL, reusing the bounded cache when available."""
        cache = getattr(self, "_statement_cache", None)
        key = (self.type, bool(has_params), query)
        return get_prepared_statement(
            cache, key, lambda: self._compile_query(query, has_params)
        )

    def execute(
        self: _DatabaseExecutionProtocol,
        query: str,
        params: Optional[Tuple] = None,
        auto_commit: bool = True,
    ) -> DbCursor:
        if self.transaction_depth > 0:
            self._validate_transaction_state()

        if _query_mutates_data(query):
            with self._query_cache_lock:
                self._query_cache.clear()

        upper_query = query.strip().upper()
        # Statement preparation (comment stripping, placeholder conversion,
        # PG type rewrites) is pure and depends only on the SQL text. Cache the
        # result so repeated hot queries pay for it once, not per request.
        query_conv = self._prepare_query(query, bool(params))

        conn: Any = None
        cursor: Any = None
        for attempt in range(3):
            conn = None
            cursor = None
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                start_time = time.time()

                self._mark_pool_in_flight(conn, True)
                try:
                    if params:
                        cursor.execute(query_conv, params)
                    else:
                        cursor.execute(query_conv)
                finally:
                    self._mark_pool_in_flight(conn, False)

                duration_ms = (time.time() - start_time) * 1000
                self.monitor.record_query_execution(duration_ms)

                _query_count.set(_query_count.get() + 1)
                _query_time_ms.set(_query_time_ms.get() + duration_ms)

                logger.debug(f"Executed query ({duration_ms:.2f}ms): {query[:100]}")

                if duration_ms > self._slow_query_threshold_ms:
                    logger.warning(
                        f"Slow query detected ({duration_ms:.2f}ms): {query[:100]}"
                    )

                # Capture the rowid at INSERT time on THIS connection so a
                # later fetch_last_insert_id() cannot observe a rowid written
                # by an interleaved statement on the same thread-local conn.
                lastrowid = getattr(cursor, "lastrowid", None)
                if (
                    self.type != "postgres"
                    and upper_query.startswith(("INSERT", "REPLACE"))
                    and lastrowid is not None
                ):
                    self._local.last_insert_rowid = lastrowid

                # Statement succeeded — leave the retry loop. The commit is
                # performed AFTER this loop (outside the retry error domain):
                # a failed commit must propagate immediately and must NEVER
                # trigger a re-execution of the statement above (the statement
                # already ran and committing it again would duplicate writes).
                # _commit_strict retries the COMMIT itself once internally.
                break
            except Exception as e:
                is_conn_error = self._is_connection_error(e)

                # Do NOT retry schema errors (e.g. "no such column", "no such table", "duplicate column")
                # Retrying is pointless since the schema won't change between attempts.
                is_schema_error = self._is_schema_error(e)

                if (
                    attempt < 2
                    and is_conn_error
                    and not self.in_transaction
                    and not is_schema_error
                ):
                    logger.warning(
                        f"Database connection error (attempt {attempt + 1}), retrying: {e}"
                    )
                    if hasattr(self._local, "connection") and self._local.connection:
                        try:
                            self.engine.close_connection(
                                self._local.connection, self._pool, {"close": True}
                            )
                        except Exception as close_error:
                            logger.debug(
                                f"reconnect: old connection close failed: {close_error}"
                            )
                        self._local.connection = None
                    continue

                self.monitor.record_error(type(e).__name__)
                temp_conn = getattr(self._local, "connection", None)
                self._handle_execution_error(
                    temp_conn, locals().get("cursor"), e, query
                )
                raise

        if conn is None or cursor is None:
            raise RuntimeError(
                f"execute() exhausted retries without a live connection: {query[:100]}"
            )

        if auto_commit and not self.in_transaction:
            # Own error domain: failures here are raised as-is (after
            # _commit_strict's single internal retry) and are NOT caught by
            # the statement-retry handler above.
            self._commit_strict(conn, f"execute: {query[:100]}")

        # Release the pooled connection between statements (SQLite) so a
        # cores-sized pool is not starved by per-thread hoarding. ONLY for
        # non-SELECT statements: their cursors never fetch rows, and
        # rowcount/lastrowid are computed at execute() time, so the cursor
        # stays valid after the connection is returned. SELECTs release in
        # fetch_one()/fetch_all() AFTER the rows are consumed — releasing a
        # connection under a live read cursor lets another thread's statement
        # reset the statement underneath it (IndexError in fetchone).
        if (
            not upper_query.startswith(("SELECT", "PRAGMA", "WITH", "EXPLAIN"))
            and "RETURNING" not in upper_query
        ):
            self._release_conn()

        return cursor

    def execute_many(
        self: _DatabaseExecutionProtocol,
        query: str,
        params_list: List[Tuple],
        auto_commit: bool = True,
    ) -> DbCursor:
        if self.transaction_depth > 0:
            self._validate_transaction_state()

        # A batched write is still a write. execute() clears the query-result
        # cache for every data-mutating statement so a later SELECT cannot be
        # served a pre-write snapshot; executemany must do the same. Without
        # this, a sweep that updates rows via execute_many leaves every cached
        # SELECT over those rows stale until its short TTL expires -- e.g. the
        # poll expiry sweep would re-select the same "still open" rows forever.
        # One clear per batch is strictly cheaper than the N clears the old
        # per-row execute() loop paid.
        if _query_mutates_data(query):
            with self._query_cache_lock:
                self._query_cache.clear()

        # executemany always binds parameters; reuse the prepared text just as
        # execute() does (the whole batch shares one conversion).
        query_conv = self._prepare_query(query, True)

        conn: Any = None
        cursor: Any = None
        for attempt in range(3):
            conn = None
            cursor = None
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                start_time = time.time()

                self._mark_pool_in_flight(conn, True)
                try:
                    cursor.executemany(query_conv, params_list)
                finally:
                    self._mark_pool_in_flight(conn, False)
                exec_time = (time.time() - start_time) * 1000
                self.monitor.record_query_execution(exec_time)

                _query_count.set(_query_count.get() + 1)
                _query_time_ms.set(_query_time_ms.get() + exec_time)

                # See execute(): leave the retry loop on statement success so
                # the commit below can never cause a re-execution.
                break
            except Exception as e:
                is_conn_error = self._is_connection_error(e)

                # Do NOT retry schema errors (e.g. "no such column", "no such table", "duplicate column")
                # Retrying is pointless since the schema won't change between attempts.
                is_schema_error = self._is_schema_error(e)

                if (
                    attempt < 2
                    and is_conn_error
                    and not self.in_transaction
                    and not is_schema_error
                ):
                    logger.warning(
                        f"Database connection error during execute_many (attempt {attempt + 1}), retrying: {e}"
                    )
                    if hasattr(self._local, "connection") and self._local.connection:
                        try:
                            self.engine.close_connection(
                                self._local.connection, self._pool, {"close": True}
                            )
                        except Exception as close_error:
                            logger.debug(
                                f"reconnect: old connection close failed: {close_error}"
                            )
                        self._local.connection = None
                    continue

                self.monitor.record_error(type(e).__name__)
                temp_conn = getattr(self._local, "connection", None)
                self._handle_execution_error(
                    temp_conn, locals().get("cursor"), e, query
                )
                raise

        if conn is None or cursor is None:
            raise RuntimeError(
                f"execute_many() exhausted retries without a live connection: "
                f"{query[:100]}"
            )

        if auto_commit and not self.in_transaction:
            # Own error domain: commit failures propagate and must never be
            # silently discarded or retried by re-running the statement.
            self._commit_strict(conn, f"execute_many: {query[:100]}")

        # See execute(): return the connection to the pool between statements.
        self._release_conn()

        return cursor

    def _is_connection_error(
        self: _DatabaseExecutionProtocol, e: BaseException
    ) -> bool:
        """Return True if the exception looks like a real connection/transport
        failure that is worth retrying.

        This deliberately avoids matching sqlite3.OperationalError by type name
        alone — many OperationalError subclasses (e.g. "cannot commit - no
        transaction is active") are not connection errors. We only retry when
        the message itself signals disconnection, OR the exception type is
        InterfaceError/DatabaseError, OR OperationalError with a connection
        message.
        """
        err_str = str(e).lower()
        type_name = type(e).__name__
        if any(
            phrase in err_str
            for phrase in [
                "connection refused",
                "connection reset",
                "connection closed",
                "closed database",
                "no connection",
                "disconnected",
                "broken pipe",
                "lost connection",
                "database is locked",
            ]
        ):
            return True
        if "InterfaceError" in type_name:
            return True
        if "DatabaseError" in type_name and "connection" in err_str:
            return True
        if "OperationalError" in type_name and any(
            p in err_str
            for p in ["connection", "closed", "disconnected", "broken pipe"]
        ):
            return True
        return False

    def _is_schema_error(self: _DatabaseExecutionProtocol, e: BaseException) -> bool:
        """Return True if the exception looks like a schema error that should
        NOT be retried (the schema will not change between attempts).
        """
        err_str = str(e).lower()
        return any(
            phrase in err_str
            for phrase in [
                "no such column",
                "no such table",
                "no such index",
                "no such view",
                "duplicate column",
                "already exists",
            ]
        )

    def _cache_get(self: _DatabaseExecutionProtocol, key: str) -> Optional[Any]:
        """LRU get from the query cache. Returns None on miss or expiry."""
        with self._query_cache_lock:
            if key not in self._query_cache:
                return None
            expiry, result = self._query_cache[key]
            if time.time() >= expiry:
                del self._query_cache[key]
                return None
            self._query_cache.move_to_end(key)
            return result

    def _cache_put(
        self: _DatabaseExecutionProtocol, key: str, value: Any, ttl: float
    ) -> None:
        """LRU put into the query cache with size-bounded eviction."""
        with self._query_cache_lock:
            self._query_cache[key] = (time.time() + ttl, value)
            self._query_cache.move_to_end(key)
            while len(self._query_cache) > _QUERY_CACHE_MAX:
                self._query_cache.popitem(last=False)

    def fetch_one(
        self: _DatabaseExecutionProtocol, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]:
        cache_key = f"one:{query}:{params}"

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        cursor = self.execute(query, params)
        result = cursor.fetchone()

        # Capture column names BEFORE closing the cursor, since cursor.description
        # becomes None after close() on most database drivers (e.g. sqlite3).
        if result is None:
            final_result = None
        elif hasattr(result, "keys"):
            final_result = dict(result)
        else:
            column_names = (
                [desc[0] for desc in cursor.description] if cursor.description else []
            )
            if column_names:
                final_result = dict(zip(column_names, result))
            else:
                logger.error(
                    "fetch_one: Could not determine column names for query "
                    "(cursor.description unavailable after close). Query: %s. "
                    "This usually indicates a database driver compatibility issue.",
                    query[:200],
                )
                # Return None instead of an empty dict to signal failure.
                # Empty dicts cause confusing KeyErrors downstream, whereas
                # callers already handle None (no row found).
                final_result = None
                # Do NOT cache this error result — the underlying issue may
                # be transient, and caching None would mask real data.
                cursor.close()
                self._release_conn()
                return final_result

        cursor.close()

        # Rows consumed — the connection is safe to return to the pool now
        # (see execute(): releasing under a live read cursor is unsafe).
        self._release_conn()

        self._cache_put(cache_key, final_result, self._query_cache_ttl)

        return final_result

    def fetch_all(
        self: _DatabaseExecutionProtocol, query: str, params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        cache_key = f"all:{query}:{params}"

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        cursor = self.execute(query, params)
        results = cursor.fetchall()

        # Capture column names BEFORE closing the cursor, since cursor.description
        # becomes None after close() on most database drivers (e.g. sqlite3).
        if results and hasattr(results[0], "keys"):
            final_results = [dict(row) for row in results]
        elif results:
            column_names = (
                [desc[0] for desc in cursor.description] if cursor.description else []
            )
            if column_names:
                final_results = [dict(zip(column_names, row)) for row in results]
            else:
                logger.error(
                    "fetch_all: Could not determine column names for %d result(s) "
                    "(cursor.description unavailable after close). Query: %s. "
                    "This usually indicates a database driver compatibility issue. "
                    "Returning empty list to avoid confusing KeyErrors downstream.",
                    len(results),
                    query[:200],
                )
                final_results = []
                # Do NOT cache this error result — the underlying issue may
                # be transient, and caching an empty list would mask real data.
                cursor.close()
                self._release_conn()
                return final_results
        else:
            final_results = []

        cursor.close()

        # Rows consumed — the connection is safe to return to the pool now.
        self._release_conn()

        self._cache_put(cache_key, final_results, self._query_cache_ttl)

        return final_results

    def fetch_many_by_ids(
        self: _DatabaseExecutionProtocol,
        table: str,
        ids: Any,
        key_column: str = "id",
        columns: Optional[List[str]] = None,
        batch_size: int = _MAX_BATCH_PARAMS,
    ) -> Dict[Any, Dict[str, Any]]:
        """Batch-load rows by key in O(ceil(n/batch)) queries, never O(n).

        This is the data-layer primitive for the N+1 read pattern that shows up
        in the hot repository paths::

            out = {}
            for entity_id in ids:
                row = db.fetch_one("SELECT * FROM things WHERE id = ?",
                                   (entity_id,))
                if row:
                    out[entity_id] = row

        With ``n`` ids that issues ``n`` statements, each paying a full
        execute/commit/cache round trip. One ``IN (?, ?, ...)`` statement per
        bounded chunk returns the same rows in a handful of round trips. The
        returned mapping is keyed by ``key_column``; ids with no row are simply
        absent, so callers can distinguish "not found" without a second query.

        ``table``, ``key_column`` and ``columns`` are validated as identifiers
        before interpolation, so the only dynamic part of the SQL is the
        placeholder list -- values always travel as bound parameters.
        """
        unique_ids: List[Any] = []
        seen = set()
        for value in ids:
            if value is None or value in seen:
                continue
            seen.add(value)
            unique_ids.append(value)
        if not unique_ids:
            return {}

        chunk_size = bounded_int(
            batch_size, "batch_size", minimum=1, maximum=_MAX_BATCH_PARAMS
        )
        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_key = dialect.sanitize_identifier(key_column, self.type)

        if columns:
            projected = list(columns)
            if key_column not in projected:
                # The key must be selected for the caller to get a mapping.
                projected.insert(0, key_column)
            safe_columns = [
                dialect.sanitize_identifier(column, self.type) for column in projected
            ]
            projection = ", ".join(safe_columns)
        else:
            projection = "*"

        result: Dict[Any, Dict[str, Any]] = {}
        for start in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[start : start + chunk_size]
            placeholders = ", ".join("?" for _ in chunk)
            query = (
                f"SELECT {projection} FROM {safe_table} "
                f"WHERE {safe_key} IN ({placeholders})"
            )
            for row in self.fetch_all(query, tuple(chunk)):
                key = row.get(key_column) if isinstance(row, dict) else None
                if key is not None:
                    result[key] = row
        return result

    def table_exists(self: _DatabaseExecutionProtocol, table_name: str) -> bool:
        query, params = self.engine.get_table_exists_query(table_name)
        result = self.fetch_one(query, params)
        return result is not None

    def insert_or_ignore(
        self: _DatabaseExecutionProtocol, table: str, columns: List[str], values: Tuple
    ) -> bool:
        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_cols = [dialect.sanitize_identifier(c, self.type) for c in columns]
        query = self.engine.get_insert_or_ignore_query(safe_table, safe_cols)
        cursor = self.execute(query, values)
        count = cursor.rowcount
        cursor.close()
        return count > 0

    def upsert(
        self: _DatabaseExecutionProtocol,
        table: str,
        columns: List[str],
        values: Tuple,
        conflict_columns: List[str],
        update_columns: Optional[List[str]] = None,
    ) -> None:
        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_cols = [dialect.sanitize_identifier(c, self.type) for c in columns]
        safe_conflict = [
            dialect.sanitize_identifier(c, self.type) for c in conflict_columns
        ]

        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]
        safe_updates = [
            dialect.sanitize_identifier(c, self.type) for c in update_columns
        ]

        if not safe_updates:
            self.insert_or_ignore(table, columns, values)
            return

        query = self.engine.get_upsert_query(
            safe_table, safe_cols, safe_conflict, safe_updates
        )
        cursor = self.execute(query, values)
        cursor.close()

    def upsert_many(
        self: _DatabaseExecutionProtocol,
        table: str,
        columns: List[str],
        values_list: List[Tuple],
        conflict_columns: List[str],
        update_columns: Optional[List[str]] = None,
        auto_commit: bool = True,
    ) -> None:
        """Batched upsert: one statement/commit for all rows, not one per row.

        Mirrors :meth:`upsert`'s identifier sanitization and engine-specific
        SQL, but writes every row through a single ``execute_many`` call. A
        full reindex used to call ``upsert`` once per message (one write
        statement and one commit each); batching collapses a page of N rows to
        one round trip while preserving the same ON CONFLICT semantics.

        ``execute_many`` also clears the query-result cache once for the whole
        batch, so a later SELECT cannot be served a pre-write snapshot (cache
        coherence after a batched write). Fail-closed: a constraint failure in
        any row propagates out of the batch instead of being silently skipped.
        """
        if not values_list:
            return

        safe_table = dialect.sanitize_identifier(table, self.type)
        safe_cols = [dialect.sanitize_identifier(c, self.type) for c in columns]
        safe_conflict = [
            dialect.sanitize_identifier(c, self.type) for c in conflict_columns
        ]

        if update_columns is None:
            update_columns = [c for c in columns if c not in conflict_columns]
        safe_updates = [
            dialect.sanitize_identifier(c, self.type) for c in update_columns
        ]

        if not safe_updates:
            query = self.engine.get_insert_or_ignore_query(safe_table, safe_cols)
        else:
            query = self.engine.get_upsert_query(
                safe_table, safe_cols, safe_conflict, safe_updates
            )
        self.execute_many(query, values_list, auto_commit=auto_commit)
