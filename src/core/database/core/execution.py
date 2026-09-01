import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Protocol, Tuple

import utils.logger as logger

from .. import dialect
from .metrics import _query_count, _query_time_ms
from .types import DbConnection, DbCursor

_QUERY_CACHE_MAX = 100

_DDL_STATEMENT_PREFIXES = ("CREATE", "ALTER")

# Splits a SQL string into alternating code / single-quoted-literal segments
# ('' inside a literal is an escaped quote). Substitutions are applied ONLY to
# the code segments so string-literal data is never rewritten.
_SQL_LITERAL_SPLIT_RE = re.compile(r"('(?:[^']|'')*')")


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

    def fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]: ...

    def insert_or_ignore(
        self, table: str, columns: List[str], values: Tuple
    ) -> bool: ...

    def _sanitize_sqlite_query(self, query: str) -> str: ...

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
            except Exception:
                pass

    def execute(
        self: _DatabaseExecutionProtocol,
        query: str,
        params: Optional[Tuple] = None,
        auto_commit: bool = True,
    ) -> DbCursor:
        if self.transaction_depth > 0:
            self._validate_transaction_state()

        upper_query = query.strip().upper()
        if any(
            upper_query.startswith(word)
            for word in [
                "INSERT",
                "UPDATE",
                "DELETE",
                "REPLACE",
                "DROP",
                "CREATE",
                "ALTER",
            ]
        ):
            with self._query_cache_lock:
                self._query_cache.clear()

        query_sanitized = self._sanitize_sqlite_query(query)
        query_conv = dialect.convert_placeholders(query_sanitized, self.type)

        if self.type == "postgres":
            query_conv = apply_pg_type_fixes(query_conv)

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
                        except Exception:
                            pass
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
        if not upper_query.startswith(
            ("SELECT", "PRAGMA", "WITH", "EXPLAIN")
        ) and "RETURNING" not in upper_query:
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

        query_sanitized = self._sanitize_sqlite_query(query)
        query_conv = dialect.convert_placeholders(query_sanitized, self.type)

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
                        except Exception:
                            pass
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
