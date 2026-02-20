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
    def transaction_depth(self) -> int:
        ...

    @transaction_depth.setter
    def transaction_depth(self, value: int) -> None:
        ...

    @property
    def in_transaction(self) -> bool:
        ...

    @in_transaction.setter
    def in_transaction(self, value: bool) -> None:
        ...

    def _get_conn(self, auto_connect: bool = True) -> DbConnection:
        ...

    def _handle_execution_error(
        self, conn: DbConnection, cursor: DbCursor, e: Exception, query: str
    ) -> None:
        ...

    def _validate_transaction_state(self) -> None:
        ...

    def execute(
        self, query: str, params: Optional[Tuple] = None, auto_commit: bool = True
    ) -> DbCursor:
        ...

    def fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]:
        ...

    def insert_or_ignore(self, table: str, columns: List[str], values: Tuple) -> bool:
        ...


class DatabaseExecutionMixin:
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

        query_conv = dialect.convert_placeholders(query, self.type)

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
                    conn.commit()

                return cursor
            except Exception as e:
                is_conn_error = (
                    "OperationalError" in type(e).__name__ or "closed" in str(e).lower()
                )

                if attempt < 2 and is_conn_error and not self.in_transaction:
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

    def execute_many(
        self: _DatabaseExecutionProtocol,
        query: str,
        params_list: List[Tuple],
        auto_commit: bool = True,
    ) -> DbCursor:
        if self.transaction_depth > 0:
            self._validate_transaction_state()

        query_conv = dialect.convert_placeholders(query, self.type)

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
                    conn.commit()
                return cursor
            except Exception as e:
                is_conn_error = (
                    "OperationalError" in type(e).__name__ or "closed" in str(e).lower()
                )

                if attempt < 2 and is_conn_error and not self.in_transaction:
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
        cursor.close()

        final_result = dict(result) if result else None

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
        cursor.close()

        final_results = [dict(row) for row in results]

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
