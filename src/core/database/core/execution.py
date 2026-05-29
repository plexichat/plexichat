import time
from typing import Any, Dict, List, Optional, Protocol, Tuple

import utils.logger as logger

from .. import dialect
from .metrics import _query_count, _query_time_ms
from .types import DbConnection, DbCursor


class _DatabaseExecutionProtocol(Protocol):
    _query_cache: Dict[str, Tuple[float, Any]]
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

    def execute(
        self: _DatabaseExecutionProtocol,
        query: str,
        params: Optional[Tuple] = None,
        auto_commit: bool = True,
    ) -> DbCursor:
        if self.transaction_depth > 0:
            self._validate_transaction_state()  # pyright: ignore[reportAttributeAccessIssue]

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

        for attempt in range(3):
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                start_time = time.time()

                if params:
                    cursor.execute(query_conv, params)
                else:
                    cursor.execute(query_conv)

                duration_ms = (time.time() - start_time) * 1000
                self.monitor.record_query_execution(duration_ms)

                _query_count.set(_query_count.get() + 1)
                _query_time_ms.set(_query_time_ms.get() + duration_ms)

                if duration_ms > self._slow_query_threshold_ms:
                    logger.warning(
                        f"Slow query detected ({duration_ms:.2f}ms): {query[:100]}"
                    )

                if auto_commit and not self.in_transaction:
                    try:
                        conn.commit()
                    except Exception:
                        pass

                return cursor
            except Exception as e:
                is_conn_error = (
                    "OperationalError" in type(e).__name__ or "closed" in str(e).lower()
                )

                # Do NOT retry schema errors (e.g. "no such column", "no such table", "duplicate column")
                # Retrying is pointless since the schema won't change between attempts.
                err_str = str(e).lower()
                is_schema_error = any(
                    phrase in err_str
                    for phrase in [
                        "no such column",
                        "no such table",
                        "duplicate column",
                        "already exists",
                    ]
                )

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
                self._handle_execution_error(  # pyright: ignore[reportAttributeAccessIssue]
                    temp_conn, locals().get("cursor"), e, query
                )
                raise

    def execute_many(
        self: _DatabaseExecutionProtocol,
        query: str,
        params_list: List[Tuple],
        auto_commit: bool = True,
    ) -> DbCursor:
        if self.transaction_depth > 0:
            self._validate_transaction_state()  # pyright: ignore[reportAttributeAccessIssue]

        query_sanitized = self._sanitize_sqlite_query(query)
        query_conv = dialect.convert_placeholders(query_sanitized, self.type)

        for attempt in range(3):
            try:
                conn = self._get_conn()
                cursor = conn.cursor()
                start_time = time.time()

                cursor.executemany(query_conv, params_list)
                exec_time = (time.time() - start_time) * 1000
                self.monitor.record_query_execution(exec_time)

                _query_count.set(_query_count.get() + 1)
                _query_time_ms.set(_query_time_ms.get() + exec_time)

                if auto_commit and not self.in_transaction:
                    try:
                        conn.commit()
                    except Exception:
                        pass
                return cursor
            except Exception as e:
                is_conn_error = (
                    "OperationalError" in type(e).__name__ or "closed" in str(e).lower()
                )

                # Do NOT retry schema errors (e.g. "no such column", "no such table", "duplicate column")
                # Retrying is pointless since the schema won't change between attempts.
                err_str = str(e).lower()
                is_schema_error = any(
                    phrase in err_str
                    for phrase in [
                        "no such column",
                        "no such table",
                        "duplicate column",
                        "already exists",
                    ]
                )

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
                self._handle_execution_error(  # pyright: ignore[reportAttributeAccessIssue]
                    temp_conn, locals().get("cursor"), e, query
                )
                raise

    def fetch_one(
        self: _DatabaseExecutionProtocol, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]:
        cache_key = f"one:{query}:{params}"
        now = time.time()

        with self._query_cache_lock:
            if cache_key in self._query_cache:
                expiry, result = self._query_cache[cache_key]
                if now < expiry:
                    return result
                del self._query_cache[cache_key]

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
                return final_result

        cursor.close()

        with self._query_cache_lock:
            self._query_cache[cache_key] = (now + self._query_cache_ttl, final_result)
            if len(self._query_cache) > 100:
                self._query_cache = {
                    k: v for k, v in self._query_cache.items() if v[0] > now
                }

        return final_result

    def fetch_all(
        self: _DatabaseExecutionProtocol, query: str, params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        cache_key = f"all:{query}:{params}"
        now = time.time()

        with self._query_cache_lock:
            if cache_key in self._query_cache:
                expiry, result = self._query_cache[cache_key]
                if now < expiry:
                    return result
                del self._query_cache[cache_key]

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
                return final_results
        else:
            final_results = []

        cursor.close()

        with self._query_cache_lock:
            self._query_cache[cache_key] = (now + self._query_cache_ttl, final_results)

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
