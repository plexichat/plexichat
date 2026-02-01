from typing import Any, Optional, Dict, List, Tuple
import time
from .base import BaseEngine
import utils.logger as logger

class PostgresEngine(BaseEngine):
    """PostgreSQL database engine implementation."""

    def connect(self, pool: Optional[Any] = None) -> Any:
        if pool:
            return pool.getconn()
            
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError:
            logger.error("psycopg2 not installed. Please install with: pip install psycopg2-binary")
            raise ImportError("psycopg2 not installed")

        pg_config = self.config.get("postgres", {})
        pool_config = self.config.get("connection_pool", {})
        connect_timeout = pool_config.get("connect_timeout", 10)
        
        # Build DSN with keepalives for stability
        dsn = f"host={pg_config.get('host', 'localhost')} " \
              f"port={pg_config.get('port', 5432)} " \
              f"user={pg_config.get('user', 'postgres')} " \
              f"password={pg_config.get('password', '')} " \
              f"dbname={pg_config.get('dbname', 'plexichat')} " \
              f"sslmode={pg_config.get('sslmode', 'prefer')} " \
              f"keepalives=1 keepalives_idle=60 keepalives_interval=10 keepalives_count=5"

        return psycopg2.connect(dsn, cursor_factory=RealDictCursor, connect_timeout=connect_timeout)

    def create_pool(self, min_conn: int, max_conn: int) -> Any:
        try:
            from psycopg2.pool import ThreadedConnectionPool
            from psycopg2.extras import RealDictCursor
        except ImportError:
            raise ImportError("psycopg2 not installed")

        pg_config = self.config.get("postgres", {})
        pool_config = self.config.get("connection_pool", {})
        connect_timeout = pool_config.get("connect_timeout", 10)

        # Build DSN with keepalives for stability
        dsn = f"host={pg_config.get('host', 'localhost')} " \
              f"port={pg_config.get('port', 5432)} " \
              f"user={pg_config.get('user', 'postgres')} " \
              f"password={pg_config.get('password', '')} " \
              f"dbname={pg_config.get('dbname', 'plexichat')} " \
              f"sslmode={pg_config.get('sslmode', 'prefer')} " \
              f"keepalives=1 keepalives_idle=60 keepalives_interval=10 keepalives_count=5"
              
        logger.info(f"Creating ThreadedConnectionPool ({min_conn}-{max_conn}) with timeout {connect_timeout}s")
        start_time = time.time()
        try:
            pool = ThreadedConnectionPool(
                min_conn, max_conn, dsn, 
                cursor_factory=RealDictCursor,
                connect_timeout=connect_timeout
            )
            elapsed = time.time() - start_time
            logger.info(f"ThreadedConnectionPool created successfully in {elapsed:.2f}s")
            return pool
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"ThreadedConnectionPool creation FAILED after {elapsed:.2f}s: {e}")
            raise

    def get_pool_stats(self, pool: Any) -> Dict[str, Any]:
        stats = {
            "active_connections": 0,
            "idle_connections": 0,
            "total_connections": 0,
            "max_connections": 0,
            "min_connections": 0,
            "utilization_percent": 0
        }
        if not pool:
            return stats

        try:
            stats["min_connections"] = getattr(pool, "minconn", 0)
            stats["max_connections"] = getattr(pool, "maxconn", 0)
            
            idle_list = getattr(pool, "_pool", [])
            stats["idle_connections"] = len(idle_list) if isinstance(idle_list, list) else 0
            
            used_list = getattr(pool, "_used", {})
            stats["active_connections"] = len(used_list) if isinstance(used_list, (list, dict)) else 0
            
            stats["total_connections"] = stats["idle_connections"] + stats["active_connections"]
            
            if stats["max_connections"] > 0:
                stats["utilization_percent"] = (stats["active_connections"] / stats["max_connections"]) * 100
        except Exception as e:
            logger.warning(f"Could not retrieve detailed pool stats: {e}")
            
        return stats

    def close_connection(self, conn: Any, pool: Optional[Any] = None, close_params: Optional[Dict[str, Any]] = None) -> None:
        if not conn:
            return
            
        if pool:
            should_close = close_params.get("close", False) if close_params else False
            try:
                pool.putconn(conn, close=should_close)
            except Exception as e:
                logger.debug(f"Could not return connection to pool: {e}")
        else:
            try:
                conn.close()
            except Exception as e:
                logger.debug(f"Could not close connection: {e}")

    def get_table_exists_query(self, table_name: str) -> Tuple[str, Tuple]:
        query = """
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema='public' AND table_name=?
        """
        return query, (table_name,)

    def get_insert_or_ignore_query(self, table: str, columns: List[str]) -> str:
        safe_cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        return f"INSERT INTO {table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    def get_upsert_query(self, table: str, columns: List[str], conflict_columns: List[str], update_columns: List[str]) -> str:
        safe_cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        conflict_cols = ", ".join(conflict_columns)
        updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_columns])
        return f"INSERT INTO {table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"
