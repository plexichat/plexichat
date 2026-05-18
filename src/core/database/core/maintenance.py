import re
from typing import Any, Optional, Protocol

import utils.logger as logger

from .. import dialect
from .types import DbConnection, DbCursor


class _DatabaseMaintenanceProtocol(Protocol):
    type: str
    _local: Any
    monitor: Any

    @property
    def transaction_depth(self) -> int: ...

    @transaction_depth.setter
    def transaction_depth(self, value: int) -> None: ...

    @property
    def in_transaction(self) -> bool: ...

    @in_transaction.setter
    def in_transaction(self, value: bool) -> None: ...

    def get_correlation_id(self) -> Optional[str]: ...
    def fetch_one(
        self, query: str, params: Optional[tuple] = None
    ) -> Optional[dict]: ...
    def fetch_all(self, query: str, params: Optional[tuple] = None) -> list: ...
    def execute(self, query: str, params: Optional[tuple] = None) -> None: ...
    def table_exists(self, table: str) -> bool: ...
    def column_exists(self, table: str, column: str) -> bool: ...
    def index_exists(self, index_name: str) -> bool: ...
    def _sanitize_identifier(self, identifier: str) -> str: ...


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
                if conn and getattr(conn, "closed", 0) > 0:
                    self.transaction_depth = 0
                    self.in_transaction = False
                elif conn and self.transaction_depth > 0:
                    try:
                        if cursor is not None:
                            cursor.execute(
                                f"ROLLBACK TO SAVEPOINT sp_{self.transaction_depth}"
                            )
                    except Exception:
                        conn.rollback()
                        self.transaction_depth = 0
                        self.in_transaction = False
                elif conn:
                    conn.rollback()
                    self.in_transaction = False
            elif conn and not self.in_transaction:
                conn.rollback()
        except Exception as cleanup_e:
            logger.error(f"Error during error cleanup: {cleanup_e}")
        finally:
            if cursor is not None:
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

    def _convert_placeholders(self: _DatabaseMaintenanceProtocol, query: str) -> str:
        return dialect.convert_placeholders(query, self.type)

    def _check_connection_age(self: _DatabaseMaintenanceProtocol, conn_id: int):
        self.monitor.check_connection_age(conn_id)

    def _format_log_context(self: _DatabaseMaintenanceProtocol, **kwargs) -> str:
        ctx = [f"{k}={v}" for k, v in kwargs.items()]
        correlation_id = self.get_correlation_id()
        if correlation_id:
            ctx.append(f"correlation_id={correlation_id}")
        return f"[{' '.join(ctx)}]"

    def table_exists(self: _DatabaseMaintenanceProtocol, table_name: str) -> bool:
        """
        Check if a table exists in the database.

        Args:
            table_name: Name of the table to check

        Returns:
            True if table exists, False otherwise
        """
        if not re.match(r"^[a-zA-Z0-9_]+$", table_name):
            logger.warning(f"Invalid table name for existence check: {table_name}")
            return False

        if self.type == "postgres":
            row = self.fetch_one(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                (table_name,),
            )
            return row is not None
        else:
            row = self.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return row is not None

    def column_exists(
        self: _DatabaseMaintenanceProtocol, table_name: str, column_name: str
    ) -> bool:
        """
        Check if a column exists in a table.

        Args:
            table_name: Name of the table
            column_name: Name of the column

        Returns:
            True if column exists, False otherwise
        """
        if not re.match(r"^[a-zA-Z0-9_]+$", table_name) or not re.match(
            r"^[a-zA-Z0-9_]+$", column_name
        ):
            logger.warning(
                f"Invalid table/column name for existence check: {table_name}.{column_name}"
            )
            return False

        if not self.table_exists(table_name):
            return False

        if self.type == "postgres":
            row = self.fetch_one(
                "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
                (table_name, column_name),
            )
            return row is not None
        else:
            safe_table = self._sanitize_identifier(table_name)
            rows = self.fetch_all(f"PRAGMA table_info({safe_table})")
            return any(row["name"] == column_name for row in rows)

    def index_exists(self: _DatabaseMaintenanceProtocol, index_name: str) -> bool:
        """
        Check if an index exists in the database.

        Args:
            index_name: Name of the index to check

        Returns:
            True if index exists, False otherwise
        """
        if not re.match(r"^[a-zA-Z0-9_]+$", index_name):
            logger.warning(f"Invalid index name for existence check: {index_name}")
            return False

        if self.type == "postgres":
            row = self.fetch_one(
                "SELECT 1 FROM pg_indexes WHERE indexname = ?",
                (index_name,),
            )
            return row is not None
        else:
            row = self.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (index_name,),
            )
            return row is not None
