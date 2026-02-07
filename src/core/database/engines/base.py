from abc import ABC, abstractmethod
from typing import Any, Optional, Dict, List, Tuple

class BaseEngine(ABC):
    """Abstract base class for database engines."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    def connect(self, pool: Optional[Any] = None) -> Any:
        """Establish a new connection, optionally from a pool."""
        pass

    @abstractmethod
    def get_pool_stats(self, pool: Any) -> Dict[str, Any]:
        """Get engine-specific pool statistics."""
        pass

    @abstractmethod
    def close_connection(self, conn: Any, pool: Optional[Any] = None, close_params: Optional[Dict[str, Any]] = None) -> None:
        """Close or return a connection to the pool."""
        pass

    @abstractmethod
    def get_table_exists_query(self, table_name: str) -> Tuple[str, Tuple]:
        """Get the query to check if a table exists."""
        pass
    
    @abstractmethod
    def get_insert_or_ignore_query(self, table: str, columns: List[str]) -> str:
        """Get engine-specific INSERT OR IGNORE query."""
        pass

    @abstractmethod
    def get_upsert_query(self, table: str, columns: List[str], conflict_columns: List[str], update_columns: List[str]) -> str:
        """Get engine-specific UPSERT query."""
        pass
