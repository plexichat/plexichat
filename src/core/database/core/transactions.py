from typing import Any, Protocol

import utils.logger as logger


class _DatabaseTransactionProtocol(Protocol):
    type: str

    @property
    def transaction_depth(self) -> int: ...

    @transaction_depth.setter
    def transaction_depth(self, value: int) -> None: ...

    @property
    def in_transaction(self) -> bool: ...

    @in_transaction.setter
    def in_transaction(self, value: bool) -> None: ...

    def _get_conn(self) -> Any: ...

    def _invalidate_query_cache(self) -> None: ...

    def _touch_pool(self, conn: Any, in_transaction: bool = False) -> None: ...

    def _is_no_transaction(self, e: Exception) -> bool: ...

    def _release_conn(self) -> None: ...


class DatabaseTransactionMixin:
    def _invalidate_query_cache(self) -> None:
        try:
            cache = getattr(self, "_query_cache", None)
            lock = getattr(self, "_query_cache_lock", None)
            if isinstance(cache, dict) and lock is not None:
                with lock:
                    cache.clear()
        except Exception:
            return

    def begin_transaction(self: _DatabaseTransactionProtocol) -> None:
        self._invalidate_query_cache()
        conn = self._get_conn()
        if self.transaction_depth == 0:
            if self.type == "sqlite":
                conn.execute("BEGIN")
            self.in_transaction = True
            self.transaction_depth = 1
            # Protect the just-opened transaction from idle reclaim even if it
            # pauses between BEGIN and its first statement. ``_touch_pool`` is
            # exception-safe and a no-op for non-SQLite pools.
            self._touch_pool(conn, in_transaction=True)
            return

        # Nested transaction
        self.transaction_depth += 1
        cursor = conn.cursor()
        cursor.execute(f"SAVEPOINT sp_{self.transaction_depth}")
        cursor.close()

    def _is_no_transaction(self, e: Exception) -> bool:
        err_str = str(e).lower()
        return (
            "cannot commit" in err_str
            or "cannot rollback" in err_str
            or "no transaction" in err_str
        )

    def commit(self: _DatabaseTransactionProtocol) -> None:
        self._invalidate_query_cache()
        conn = self._get_conn()
        if self.transaction_depth > 1:
            cursor = conn.cursor()
            try:
                cursor.execute(f"RELEASE SAVEPOINT sp_{self.transaction_depth}")
                cursor.close()
            except Exception as e:
                logger.warning(
                    f"Savepoint release failed: {e}, rolling back entire transaction"
                )
                cursor.close()
                conn.rollback()
                self.transaction_depth = 0
                self.in_transaction = False
                raise

            self.transaction_depth -= 1
            return

        if self.transaction_depth == 1:
            if self.type == "sqlite":
                try:
                    conn.execute("COMMIT")
                except Exception as e:
                    if self._is_no_transaction(e):
                        self.transaction_depth = 0
                        self.in_transaction = False
                        self._touch_pool(conn, in_transaction=False)
                        return
                    raise
            else:
                conn.commit()
            self.transaction_depth = 0
            self.in_transaction = False
            self._touch_pool(conn, in_transaction=False)
            # Transaction complete: return the connection to the pool so
            # concurrent threads are not starved by per-thread hoarding.
            self._release_conn()
        elif self.transaction_depth == 0:
            pass
        else:
            conn.commit()

    def rollback(self: _DatabaseTransactionProtocol) -> None:
        self._invalidate_query_cache()
        conn = self._get_conn()
        if self.transaction_depth > 0:
            # Nested rollback: undo only the innermost savepoint so the
            # enclosing transaction remains intact (standard savepoint
            # semantics). A full-chain rollback only happens when the
            # outermost transaction is rolled back.
            if self.transaction_depth > 1:
                cursor = conn.cursor()
                try:
                    cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
                except Exception as e:
                    if self._is_no_transaction(e):
                        # Savepoint already gone (e.g. failed transaction) —
                        # fall back to aborting the whole chain.
                        conn.rollback()
                        self.transaction_depth = 0
                        self.in_transaction = False
                        self._touch_pool(conn, in_transaction=False)
                        return
                    raise
                finally:
                    cursor.close()
                self.transaction_depth -= 1
                return

            if self.type == "sqlite":
                try:
                    conn.execute("ROLLBACK")
                except Exception as e:
                    if self._is_no_transaction(e):
                        self.transaction_depth = 0
                        self.in_transaction = False
                        self._touch_pool(conn, in_transaction=False)
                        return
                    raise
            else:
                conn.rollback()
            self.transaction_depth = 0
            self.in_transaction = False
            self._touch_pool(conn, in_transaction=False)
            # Transaction aborted: return the connection to the pool.
            self._release_conn()
        else:
            conn.rollback()
            self.in_transaction = False
