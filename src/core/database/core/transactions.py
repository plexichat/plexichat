from typing import Any, Protocol

import utils.logger as logger


class _DatabaseTransactionProtocol(Protocol):
    type: str
    engine: Any
    _pool: Any

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

    def _discard_transaction_connection(
        self: _DatabaseTransactionProtocol, conn: Any
    ) -> None:
        """Force-close ``conn`` and drop it from pool accounting.

        Used only when a COMMIT/ROLLBACK failed and the driver still reports an
        open transaction. Handing such a connection to another worker would let
        it inherit (and possibly commit) work from the failed transaction;
        force-closing releases the pool slot instead of leaking it.
        """
        engine = getattr(self, "engine", None)
        if engine is None:
            return
        pool = getattr(self, "_pool", None)
        try:
            engine.close_connection(conn, pool, {"close": True})
        except Exception as discard_error:
            logger.debug(
                f"transaction cleanup connection discard failed: {discard_error}"
            )

    def _end_transaction(
        self: _DatabaseTransactionProtocol, conn: Any, *, abort: bool = False
    ) -> None:
        """Clear transaction state and hand ``conn`` back to the pool.

        Called on *every* outermost commit/rollback exit path — success, the
        terminal ``no transaction is active`` case, and unexpected failures —
        so a failed COMMIT/ROLLBACK can never leave the connection checked out
        and flagged ``in_transaction`` in the pool. Two such failures per
        worker were enough to pin a cores-sized pool under concurrent load.
        Never raises, so the original commit/rollback error is propagated
        unchanged instead of being masked by cleanup.
        """
        try:
            if abort:
                # A COMMIT that failed for an unexpected reason may have left
                # the transaction open. Abort it before releasing so the next
                # borrower cannot inherit the failed work.
                try:
                    conn.rollback()
                except Exception:
                    pass
            self.transaction_depth = 0
            self.in_transaction = False
            self._touch_pool(conn, in_transaction=False)
            # If the driver still reports an open transaction (a failed
            # COMMIT/ROLLBACK), do not return it to the pool.
            if getattr(conn, "in_transaction", False):
                self._discard_transaction_connection(conn)
            self._release_conn()
        except Exception as cleanup_error:
            logger.debug(f"transaction cleanup failed: {cleanup_error}")

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
                # The failed savepoint aborted the whole chain. Clear the
                # pool's in-transaction marker AND hand the connection back,
                # otherwise this error path strands a checked-out connection
                # (marked in_transaction) until the idle reaper eventually
                # reclaims it. Repeated failures then starve the pool.
                self._touch_pool(conn, in_transaction=False)
                self._release_conn()
                raise

            self.transaction_depth -= 1
            return

        if self.transaction_depth == 1:
            unexpected_failure = False
            try:
                if self.type == "sqlite":
                    try:
                        conn.execute("COMMIT")
                    except Exception as e:
                        if not self._is_no_transaction(e):
                            raise
                else:
                    conn.commit()
            except Exception:
                unexpected_failure = True
                raise
            finally:
                # Always unwind markers and return the connection — including
                # the "no transaction is active" and unexpected-error paths,
                # which previously returned/raised with the connection still
                # pinned and marked in_transaction in the pool.
                self._end_transaction(conn, abort=unexpected_failure)
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
                        # The chain is gone: release the pooled connection so
                        # a failing client cannot pin pool capacity forever.
                        self._release_conn()
                        return
                    raise
                finally:
                    cursor.close()
                self.transaction_depth -= 1
                return

            try:
                if self.type == "sqlite":
                    try:
                        conn.execute("ROLLBACK")
                    except Exception as e:
                        if not self._is_no_transaction(e):
                            raise
                else:
                    conn.rollback()
            finally:
                # Always unwind markers and return the connection — including
                # the "no transaction is active" and failed-ROLLBACK paths,
                # which previously returned/raised with the connection pinned.
                self._end_transaction(conn)
        else:
            conn.rollback()
            self.in_transaction = False
            # Keep the pool's transaction marker in sync even when rollback is
            # called with no active transaction (e.g. after an earlier error
            # already unwound the depth).
            self._touch_pool(conn, in_transaction=False)
