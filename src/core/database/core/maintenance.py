from typing import Any, Optional, Protocol

import utils.logger as logger

from .. import dialect
from .types import DbConnection, DbCursor


class _DatabaseMaintenanceProtocol(Protocol):
    type: str
    _local: Any
    monitor: Any

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

    def get_correlation_id(self) -> Optional[str]:
        ...


class DatabaseMaintenanceMixin:
    def _validate_transaction_state(self: _DatabaseMaintenanceProtocol):
        if self.type == "postgres" and hasattr(self._local, "connection"):
            conn = self._local.connection
            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except Exception as e:
                err_msg = str(e)
                pg_code = getattr(e, "pgcode", None)

                is_failed = (
                    "InFailedSqlTransaction" in type(e).__name__
                    or pg_code == "25P02"
                    or "current transaction is aborted" in err_msg.lower()
                )

                if is_failed:
                    logger.warning(
                        f"Detected failed transaction state ({err_msg}), recovering..."
                    )
                    try:
                        conn.rollback()
                        logger.info(
                            "Successfully recovered from failed transaction state"
                        )
                    except Exception as rb_e:
                        logger.error(
                            f"Failed to recover from transaction error: {rb_e}"
                        )
                    finally:
                        self.transaction_depth = 0
                        self.in_transaction = False

    def _handle_execution_error(
        self: _DatabaseMaintenanceProtocol,
        conn: DbConnection,
        cursor: DbCursor,
        e: Exception,
        query: str,
    ):
        error_type = type(e).__name__
        logger.error(f"Query failed ({error_type}): {str(e)[:100]}")

        try:
            if self.type == "postgres":
                if getattr(conn, "closed", 0) > 0:
                    self.transaction_depth = 0
                    self.in_transaction = False
                elif self.transaction_depth > 0:
                    try:
                        cursor.execute(
                            f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}"
                        )
                    except Exception:
                        conn.rollback()
                        self.transaction_depth = 0
                        self.in_transaction = False
                else:
                    conn.rollback()
                    self.in_transaction = False
            elif not self.in_transaction:
                conn.rollback()
        except Exception as cleanup_e:
            logger.error(f"Error during error cleanup: {cleanup_e}")
        finally:
            cursor.close()

    def convert_schema(self: _DatabaseMaintenanceProtocol, schema: str) -> str:
        if self.type != "postgres":
            return schema

        import re

        converted = schema
        converted = re.sub(r"\bBLOB\b", "BYTEA", converted, flags=re.IGNORECASE)
        converted = re.sub(r"\bINTEGER\b", "BIGINT", converted, flags=re.IGNORECASE)

        return converted

    def _sanitize_identifier(
        self: _DatabaseMaintenanceProtocol, identifier: str
    ) -> str:
        return dialect.sanitize_identifier(identifier, self.type)

    def _convert_placeholders(
        self: _DatabaseMaintenanceProtocol, query: str
    ) -> str:
        return dialect.convert_placeholders(query, self.type)

    def _check_connection_age(self: _DatabaseMaintenanceProtocol, conn_id: int):
        self.monitor.check_connection_age(conn_id)

    def _format_log_context(self: _DatabaseMaintenanceProtocol, **kwargs) -> str:
        ctx = [f"{k}={v}" for k, v in kwargs.items()]
        correlation_id = self.get_correlation_id()
        if correlation_id:
            ctx.append(f"correlation_id={correlation_id}")
        return f"[{' '.join(ctx)}]"
