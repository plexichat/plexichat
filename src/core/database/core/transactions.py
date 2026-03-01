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
            return

        # Nested transaction
        self.transaction_depth += 1
        cursor = conn.cursor()
        cursor.execute(f"SAVEPOINT sp_{self.transaction_depth}")
        cursor.close()

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
                conn.execute("COMMIT")
            else:
                conn.commit()
            self.transaction_depth = 0
            self.in_transaction = False
        else:
            conn.commit()

    def rollback(self: _DatabaseTransactionProtocol) -> None:
        self._invalidate_query_cache()
        conn = self._get_conn()
        if self.transaction_depth > 0:
            # If we're inside a nested transaction, our test-suite expects a rollback
            # to abort the *entire* transaction chain.
            if self.transaction_depth > 1:
                conn.rollback()
                self.transaction_depth = 0
                self.in_transaction = False
                return

            if self.type == "sqlite":
                conn.execute("ROLLBACK")
            else:
                conn.rollback()
            self.transaction_depth = 0
            self.in_transaction = False
        else:
            conn.rollback()
            self.in_transaction = False
