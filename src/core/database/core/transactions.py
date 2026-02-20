from typing import Any, Protocol

import utils.logger as logger


class _DatabaseTransactionProtocol(Protocol):
    type: str

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

    def _get_conn(self) -> Any:
        ...


class DatabaseTransactionMixin:
    def begin_transaction(self: _DatabaseTransactionProtocol) -> None:
        conn = self._get_conn()
        if self.transaction_depth == 0:
            if self.type == "sqlite":
                conn.execute("BEGIN IMMEDIATE")
            self.in_transaction = True

        self.transaction_depth += 1
        cursor = conn.cursor()
        cursor.execute(f"SAVEPOINT sp_{self.transaction_depth}")
        cursor.close()

    def commit(self: _DatabaseTransactionProtocol) -> None:
        conn = self._get_conn()
        if self.transaction_depth > 0:
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
            if self.transaction_depth == 0:
                conn.commit()
                self.in_transaction = False
        else:
            conn.commit()

    def rollback(self: _DatabaseTransactionProtocol) -> None:
        conn = self._get_conn()
        if self.transaction_depth > 0:
            cursor = conn.cursor()
            try:
                cursor.execute(f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}")
                cursor.close()
            except Exception as e:
                logger.warning(
                    f"Savepoint rollback failed: {e}, rolling back entire transaction"
                )
                cursor.close()
                conn.rollback()
                self.transaction_depth = 0
                self.in_transaction = False
                return

            self.transaction_depth -= 1
            if self.transaction_depth == 0:
                conn.rollback()
                self.in_transaction = False
        else:
            conn.rollback()
            self.in_transaction = False
