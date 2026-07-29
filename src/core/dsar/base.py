"""
Base Collector Module

Provides the abstract base class for all domain-specific collectors with shared
database query helpers and serialization utilities.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Protocol


class DatabaseProtocol(Protocol):
    """Protocol defining the database interface used by collectors."""

    def fetch_one(self, query: str, params: tuple = ()) -> Dict[str, Any] | None:
        """Fetch a single row."""
        ...

    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Fetch all rows."""
        ...


class BaseCollector(ABC):
    """
    Abstract base class for all data collectors.

    Provides shared query helpers and serialization utilities.
    Each concrete collector implements the collect() method for its domain.
    """

    def __init__(self, db: DatabaseProtocol) -> None:
        self._db = db

    @abstractmethod
    def collect(self, user_id: int) -> Dict[str, Any]:
        """Collect all data for this domain for the given user."""
        ...

    def fetch_all(
        self, table: str, where: str, params: tuple = ()
    ) -> List[Dict[str, Any]]:
        """
        Fetch all rows from a table with a WHERE clause.

        Args:
            table: Table name
            where: WHERE clause (without 'WHERE')
            params: Query parameters

        Returns:
            List of dictionaries representing rows
        """
        query = f"SELECT * FROM {table} WHERE {where}"
        rows = self._db.fetch_all(query, params)
        return [dict(row) for row in rows]

    def fetch_one(
        self, table: str, where: str, params: tuple = ()
    ) -> Dict[str, Any] | None:
        """
        Fetch a single row from a table with a WHERE clause.

        Args:
            table: Table name
            where: WHERE clause (without 'WHERE')
            params: Query parameters

        Returns:
            Dictionary representing the row, or None if not found
        """
        query = f"SELECT * FROM {table} WHERE {where}"
        row = self._db.fetch_one(query, params)
        return dict(row) if row else None

    def serialize_rows(
        self, rows: List[Dict[str, Any]], redact_fields: List[str] | None = None
    ) -> List[Dict[str, Any]]:
        """
        Serialize rows with optional field redaction.

        Args:
            rows: List of row dictionaries
            redact_fields: Fields to redact (replace with "(encrypted)" or remove)

        Returns:
            List of serialized row dictionaries
        """
        if redact_fields is None:
            redact_fields = []

        result = []
        for row in rows:
            r = dict(row)
            for field in redact_fields:
                if field in r:
                    if r[field] and isinstance(r[field], str):
                        r[field] = "(encrypted)"
                    else:
                        del r[field]
            result.append(r)
        return result

    def serialize_row(
        self, row: Dict[str, Any], redact_fields: List[str] | None = None
    ) -> Dict[str, Any]:
        """
        Serialize a single row with optional field redaction.

        Args:
            row: Row dictionary
            redact_fields: Fields to redact

        Returns:
            Serialized row dictionary
        """
        rows = self.serialize_rows([row], redact_fields)
        return rows[0] if rows else {}
