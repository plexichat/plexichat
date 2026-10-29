"""INSERT query builder."""

from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

import utils.logger as logger

from .base import (
    TableQuery,
    Parameter,
    ValidationModelError,
)

T = TypeVar("T", bound=BaseModel)


class InsertQuery(TableQuery):
    """Query builder for INSERT statements."""

    def __init__(
        self,
        connection: Any,
        table_name: str,
        data: Dict[str, Any],
        db_type: str = "sqlite",
        schema_registry: Optional[Any] = None,
    ):
        super().__init__(connection, table_name, db_type, schema_registry)
        self.data = data

    def build(self) -> tuple[str, list]:
        """Build the INSERT query."""
        columns = list(self.data.keys())
        validated_columns = self._validate_identifiers_list(columns, "column")

        if self.schema_registry:
            self.schema_registry.validate_columns(self.table_name, validated_columns)

        placeholders = ", ".join(["?" for _ in columns])
        columns_str = ", ".join(validated_columns)
        sql = f"INSERT INTO {self.table_name} ({columns_str}) VALUES ({placeholders})"
        params = [Parameter(self.data[col]) for col in columns]

        return sql, params

    def validate(self, model: Type[T]) -> "InsertQuery":
        """Set validation model and validate data."""
        super().validate(model)

        try:
            model(**self.data)
            logger.debug(f"Data validated successfully against {model.__name__}")
        except ValidationError as e:
            raise ValidationModelError(f"Validation failed: {str(e)}")

        return self

    def _mark_pool_in_flight(self, conn: Any, in_flight: bool) -> None:
        """Mirror Database.execute()'s pool in-flight marker.

        The idle reaper must never close a connection that has a cursor
        mid-statement. The builder runs on a raw connection and has no
        facade-bound pool reference, so this is best-effort via
        ``self.database._pool`` (absent in standalone usage).
        """
        pool = getattr(self.database, "_pool", None)
        marker = getattr(pool, "mark_in_flight", None)
        if not callable(marker):
            return
        try:
            marker(conn, in_flight)
        except Exception as e:
            logger.debug(f"Failed to mark connection in-flight: {e}")

    def _capture_lastrowid(self, lastrowid: Any) -> None:
        """Store the INSERT's rowid on the owning Database's thread-local
        slot so fetch_last_insert_id() observes THIS insert even if other
        statements interleave on the same thread-local connection."""
        local = getattr(self.database, "_local", None)
        if local is not None and lastrowid is not None:
            try:
                local.last_insert_rowid = lastrowid
            except Exception as e:
                logger.debug(f"Failed to capture lastrowid: {e}")

    def execute(self) -> int:
        """Execute the INSERT query and return the last inserted row ID.

        Both backends return the new row's id, not a row count. SQLite gets
        it from ``cursor.lastrowid``; PostgreSQL has no such attribute, so the
        statement asks for the row back (``RETURNING *``) and reads ``id`` off
        it. Returning PostgreSQL's ``rowcount`` here — which was ``1`` on every
        insert — made callers that treat the result as an id fetch row 1 of the
        table instead of the row they just wrote.
        """
        sql, params = self.build()

        sanitized_params = [p.value if isinstance(p, Parameter) else p for p in params]
        logger.debug(f"Executing INSERT: {sql} with params: {sanitized_params}")

        try:
            if self.db_type == "sqlite":
                cursor = self.connection.cursor()
                param_values = [
                    p.value if isinstance(p, Parameter) else p for p in params
                ]
                self._mark_pool_in_flight(self.connection, True)
                try:
                    cursor.execute(sql, param_values)
                finally:
                    self._mark_pool_in_flight(self.connection, False)
                # Capture the rowid at INSERT time (before commit/cleanup).
                lastrowid = int(getattr(cursor, "lastrowid", 0) or 0)
                self._capture_lastrowid(lastrowid)
                if self._should_autocommit():
                    self.connection.commit()
                cursor.close()
                self._invalidate_database_query_cache()
                logger.debug(f"INSERT returned lastrowid: {lastrowid}")
                return lastrowid
            else:  # postgres
                cursor = self.connection.cursor()
                try:
                    param_values = [
                        p.value if isinstance(p, Parameter) else p for p in params
                    ]
                    from .. import dialect

                    pg_sql = dialect.convert_placeholders(sql, self.db_type)
                    # ``RETURNING *`` rather than ``RETURNING id`` because not
                    # every table this builder writes to has an ``id`` column,
                    # and asking for one that is absent would fail the INSERT.
                    pg_sql = f"{pg_sql} RETURNING *"
                    self._mark_pool_in_flight(self.connection, True)
                    try:
                        cursor.execute(pg_sql, param_values)
                        row = cursor.fetchone()
                    finally:
                        self._mark_pool_in_flight(self.connection, False)
                    new_id = 0
                    if row is not None:
                        try:
                            new_id = int(row.get("id") or 0)
                        except (TypeError, ValueError, AttributeError):
                            new_id = 0
                    if self._should_autocommit():
                        self.connection.commit()
                    self._invalidate_database_query_cache()
                    logger.debug(f"INSERT returned id: {new_id}")
                    return new_id
                finally:
                    cursor.close()
        except Exception as e:
            logger.error(f"INSERT execution failed: {str(e)}")
            # Only roll back when the builder owns the transaction; inside an
            # enclosing transaction the caller decides whether to abort.
            if self._should_autocommit():
                try:
                    self.connection.rollback()
                except Exception:
                    pass
            raise
