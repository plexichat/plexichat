import threading
import time
import uuid
from typing import Any, Dict, Optional, Protocol

import utils.logger as logger

from ..engines.postgres import PostgresEngine
from ..engines.sqlite import MONITOR_IDLE_RECLAIM_SECONDS
from .types import DbConnection


class _DatabaseConnectionProtocol(Protocol):
    engine: Any
    _pool: Any
    _local: Any
    _lock: Any
    config: Dict[str, Any]
    monitor: Any
    _max_idle_time: float
    _max_connection_age_seconds: float
    _enable_validation: bool
    _validation_interval: float
    _validation_query: str
    type: str

    @property
    def transaction_depth(self) -> int: ...

    @transaction_depth.setter
    def transaction_depth(self, value: int) -> None: ...

    @property
    def in_transaction(self) -> bool: ...

    @in_transaction.setter
    def in_transaction(self, value: bool) -> None: ...

    def get_correlation_id(self) -> Optional[str]: ...

    def set_correlation_id(self, correlation_id: str) -> None: ...

    def _generate_correlation_id(self) -> str: ...

    def connect(self) -> Optional[DbConnection]: ...

    def get_pool_stats(self) -> Dict[str, Any]: ...

    def reap_connections(self) -> int: ...

    def _touch_pool(self, conn: Any, in_transaction: bool = False) -> None: ...


class DatabaseConnectionMixin:
    def get_pool_stats(self: _DatabaseConnectionProtocol) -> Dict[str, Any]:
        engine_stats = self.engine.get_pool_stats(self._pool)
        return self.monitor.get_pool_stats(engine_stats)

    def _get_conn(
        self: _DatabaseConnectionProtocol, auto_connect: bool = True
    ) -> DbConnection:
        assert self.engine is not None, "Database engine not initialized"
        if self.get_correlation_id() is None:
            self.set_correlation_id(self._generate_correlation_id())

        if hasattr(self._local, "connection") and self._local.connection is not None:
            conn = self._local.connection
            is_valid = True
            conn_id = id(conn)

            now = time.time()
            last_valid_check = getattr(self._local, "last_valid_check", 0)
            fast_path = (now - last_valid_check) < 30.0

            # Cheap health checks run on EVERY access so eviction rules
            # (closed connections, max idle time, max connection age) are
            # honored even inside the 30s validation window.
            if getattr(conn, "closed", 0) != 0:
                logger.debug(f"Connection {conn_id} is closed, reconnecting")
                is_valid = False
            elif not hasattr(conn, "closed"):
                # Python 3.14+ removed the ``.closed`` attribute from
                # sqlite3.Connection; probe with a side-effect-free attribute
                # read that raises ProgrammingError once the connection is
                # closed (``in_transaction`` is available on sqlite3 and
                # psycopg2 connections).
                try:
                    _ = conn.in_transaction
                except Exception:
                    logger.debug(f"Connection {conn_id} is closed, reconnecting")
                    is_valid = False

            metadata = self.monitor.get_connection_metadata(conn_id)
            if is_valid and metadata:
                last_used = metadata.get("last_used", now)
                if self._max_idle_time > 0 and (now - last_used) > self._max_idle_time:
                    logger.warning(
                        f"Connection {conn_id} exceeded max idle time, evicting"
                    )
                    is_valid = False

                elif self._max_connection_age_seconds > 0:
                    created_at = metadata.get("created_at", 0)
                    if (now - created_at) > self._max_connection_age_seconds:
                        logger.warning(
                            f"Connection {conn_id} exceeded max age ({now - created_at:.1f}s), evicting"
                        )
                        is_valid = False

            # Fast path: skip the expensive liveness checks (postgres poll,
            # SELECT 1) for connections touched within the validation window.
            if is_valid and fast_path:
                self._local.last_valid_check = now
                self.monitor.update_connection_last_used(conn_id)
                self._touch_pool(conn, in_transaction=bool(self.in_transaction))
                return conn

            if is_valid and self.type == "postgres":
                if getattr(conn, "closed", 0) != 0:
                    is_valid = False

                if is_valid and hasattr(conn, "poll"):
                    PsycopgOperationalError = Exception
                    try:
                        from psycopg2 import OperationalError as PsycopgOperationalError
                    except Exception:
                        pass
                    try:
                        conn.poll()
                    except PsycopgOperationalError as e:
                        logger.debug(f"Connection {conn_id} failed poll check: {e}")
                        is_valid = False
                    except Exception as e:
                        logger.debug(f"Connection {conn_id} failed poll check: {e}")
                        is_valid = False

            if is_valid and metadata and self._enable_validation:
                last_val = metadata.get("last_validation", 0)
                if (now - last_val) >= self._validation_interval:
                    try:
                        cursor = conn.cursor()
                        cursor.execute(self._validation_query)
                        cursor.close()
                        metadata["last_validation"] = now
                    except Exception as e:
                        logger.warning(f"Connection {conn_id} failed validation: {e}")
                        is_valid = False

            if is_valid:
                self._local.last_valid_check = now
                self.monitor.update_connection_last_used(conn_id)
                self._touch_pool(conn, in_transaction=bool(self.in_transaction))
                return conn
            else:
                logger.warning(
                    f"Thread-local connection {conn_id} invalid, reconnecting"
                )
                try:
                    self.engine.close_connection(conn, self._pool, {"close": True})
                except Exception:
                    pass
                self._local.connection = None
                self._local.last_valid_check = 0
                self._local.last_validation = 0

        if not auto_connect:
            raise ConnectionError("Database not connected. Call connect() first.")

        self.connect()
        return self._local.connection

    def _touch_pool(
        self: _DatabaseConnectionProtocol,
        conn: DbConnection,
        in_transaction: bool = False,
    ) -> None:
        """Refresh the pool's idle tracking for ``conn`` (SQLite pools only).

        Called on every connection acquisition/use so actively-used
        connections are never reclaimed as idle. ``in_transaction`` marks
        connections whose owner is inside a transaction so open transactions
        are never reclaimed mid-flight. Never allowed to break the query path.
        """
        touch = getattr(self._pool, "touch", None)
        if callable(touch):
            try:
                touch(conn, in_transaction)
            except Exception:
                pass

    def connect(self: _DatabaseConnectionProtocol) -> Optional[DbConnection]:
        with self._lock:
            if hasattr(self._local, "connection") and self._local.connection:
                try:
                    self.engine.close_connection(
                        self._local.connection, self._pool, {"close": True}
                    )
                except Exception:
                    pass
                self._local.connection = None

            logger.info("Acquiring new database connection")

            start_time = time.time()
            if self.type == "postgres" and isinstance(self.engine, PostgresEngine):
                if not self._pool:
                    pool_config = self.config.get("connection_pool", {})
                    min_conn = pool_config.get("min_connections", 20)
                    self._pool = self.engine.create_pool(
                        min_conn, pool_config.get("max_connections", 300)
                    )
                pool_config = self.config.get("connection_pool", {})
                connect_timeout = pool_config.get("connect_timeout", 10)
                try:
                    conn = self.engine.connect(self._pool)
                except Exception as e:
                    err_str = str(e).lower()
                    if "pool" in err_str and (
                        "exhaust" in err_str or "no available" in err_str
                    ):
                        logger.error(
                            "Connection pool exhausted: no available connections"
                        )
                    elif "timeout" in err_str:
                        logger.error(f"Connection timeout ({connect_timeout}s): {e}")
                    else:
                        logger.error(f"Failed to acquire PostgreSQL connection: {e}")
                    raise
            elif self.type == "sqlite":
                if not self._pool:
                    pool_config = self.config.get("connection_pool", {})
                    min_conn = pool_config.get("min_connections", 2)
                    max_conn = pool_config.get("max_connections", 16)
                    self._pool = self.engine.create_pool(min_conn, max_conn)
                pool_config = self.config.get("connection_pool", {})
                connect_timeout = pool_config.get("connect_timeout", 10)
                try:
                    conn = self.engine.connect(self._pool)
                except Exception as e:
                    err_str = str(e).lower()
                    if "pool" in err_str and (
                        "exhaust" in err_str or "no available" in err_str
                    ):
                        logger.error(
                            "SQLite connection pool exhausted: no available connections"
                        )
                    elif "timeout" in err_str:
                        logger.error(f"Connection timeout: {e}")
                    else:
                        logger.error(f"Failed to acquire SQLite connection: {e}")
                    raise
            else:
                conn = self.engine.connect()

            duration = time.time() - start_time
            self.monitor.record_acquisition(duration)

            self._local.connection = conn
            self._local.last_valid_check = time.time()
            self.transaction_depth = 0
            self.in_transaction = False

            conn_id = id(conn)
            self.monitor.add_connection_metadata(
                conn_id,
                {
                    "connection": conn,
                    "created_at": time.time(),
                    "last_used": time.time(),
                    "thread_id": threading.current_thread().ident,
                    "acquired_at": time.time(),
                },
            )
            self._touch_pool(conn, in_transaction=bool(self.in_transaction))

            logger.info(
                "Connected to "
                + ("PostgreSQL" if self.type == "postgres" else "SQLite")
            )

            return conn

    def close_connection(
        self: _DatabaseConnectionProtocol,
        conn: DbConnection,
        pool: Optional[Any] = None,
        params: Optional[Dict] = None,
    ) -> None:
        if self.engine is None:
            return
        self.engine.close_connection(conn, pool, params)

    def close(self: _DatabaseConnectionProtocol, force: bool = False) -> None:
        # NOTE: ``force`` is effectively deprecated — PostgreSQL computes its
        # own force-close from connection age, and SQLite always releases the
        # thread-local connection now (see the sqlite branch below). It is
        # kept for API compatibility with existing callers.
        conn = None
        if hasattr(self._local, "connection"):
            conn = self._local.connection

        if conn:
            conn_id = id(conn)

            is_closed = False
            if self.type == "postgres":
                is_closed = getattr(conn, "closed", 0) != 0

            if not is_closed and (
                getattr(self._local, "in_transaction", False)
                or getattr(self._local, "transaction_depth", 0) > 0
            ):
                try:
                    conn.rollback()
                except Exception:
                    pass

            if self.type == "sqlite" and not is_closed:
                try:
                    conn.execute("PRAGMA optimize;")
                except Exception:
                    logger.debug(
                        f"PRAGMA optimize failed on connection {conn_id}", exc_info=True
                    )

            if self.type == "postgres":
                force_close = False
                metadata = self.monitor.get_connection_metadata(conn_id)
                if metadata and self._max_connection_age_seconds > 0:
                    age = time.time() - metadata.get("created_at", 0)
                    if age > self._max_connection_age_seconds:
                        logger.info(
                            f"Closing connection {conn_id} on thread exit (age: {age:.1f}s > {self._max_connection_age_seconds}s)"
                        )
                        force_close = True

                try:
                    self.engine.close_connection(
                        conn, self._pool, {"close": force_close}
                    )
                except Exception as e:
                    logger.error(f"Error returning connection {conn_id} to pool: {e}")
                finally:
                    self.monitor.remove_connection_metadata(conn_id)
                self._local.connection = None
            elif self.type == "sqlite":
                # SQLite: always release the thread-local connection so
                # ``close()`` actually leaves the instance disconnected
                # (state cleared), as callers and tests expect.
                #
                # ``force`` is intentionally IGNORED here: a non-force close
                # previously kept the connection warm in thread-local, which
                # (a) contradicted the ``cleared thread-local connection
                # state`` log emitted at the end of ``close()`` and (b) made
                # tests asserting ``connection is None`` after close fail.
                # ``close_params {"close": True}`` force-closes the sqlite
                # connection (removing it from the pool's accounting) so the
                # next ``connect()``/``_get_conn()`` transparently re-acquires
                # a fresh pooled connection.
                #
                # Production hot path is unaffected: DatabaseMiddleware keeps
                # SQLite connections alive in thread-local across requests and
                # only calls ``close()`` for PostgreSQL. This ``close()`` is
                # hit by explicit/error-path cleanup, migrations CLI, and
                # shutdown, where destroying the connection is correct.
                try:
                    self.engine.close_connection(conn, self._pool, {"close": True})
                except Exception as e:
                    logger.error(f"Error closing SQLite connection {conn_id}: {e}")
                finally:
                    self.monitor.remove_connection_metadata(conn_id)
                self._local.connection = None

        self._local.transaction_depth = 0
        self._local.in_transaction = False
        self._local.correlation_id = None

        self.transaction_depth = 0
        self.in_transaction = False

        logger.info("cleared thread-local connection state")

    def start_pool_monitoring(self: _DatabaseConnectionProtocol):
        self.monitor.start_pool_monitoring(self.get_pool_stats, self.reap_connections)

    def reap_connections(self: _DatabaseConnectionProtocol) -> int:
        if not self._pool:
            return 0

        reaped_count = 0
        current_time = time.time()

        connection_ids = list(self.monitor._connection_metadata.keys())

        for conn_id in connection_ids:
            metadata = self.monitor.get_connection_metadata(conn_id)
            if not metadata or "connection" not in metadata:
                continue

            last_used = metadata.get("last_used", 0)
            idle_time = current_time - last_used

            is_current_thread_conn = (
                hasattr(self._local, "connection")
                and id(self._local.connection) == conn_id
            )

            if idle_time > self._max_idle_time and not is_current_thread_conn:
                conn = metadata["connection"]
                logger.warning(
                    f"Reaping leaked/idle connection {conn_id} (idle {idle_time:.1f}s)"
                )
                try:
                    self.engine.close_connection(conn, self._pool, {"close": True})
                    reaped_count += 1
                except Exception as e:
                    logger.error(f"Error reaping connection {conn_id}: {e}")
                finally:
                    self.monitor.remove_connection_metadata(conn_id)

        # Also reclaim checked-out connections that went idle (thread-local
        # conns never returned by worker threads), so leaked connections cannot
        # permanently starve the pool even between exhaustion events. Active
        # connections are protected: the Database layer refreshes their usage
        # time on every query via ``pool.touch``. The monitor uses a longer
        # window than the exhaustion-time reclaim so the middleware's
        # keep-alive connections on request threads are not churned.
        try:
            pool_reap = getattr(self._pool, "reap_idle", None)
            if callable(pool_reap):
                reaped = pool_reap(MONITOR_IDLE_RECLAIM_SECONDS)
                if isinstance(reaped, int):
                    reaped_count += reaped
        except Exception:
            pass

        return reaped_count

    def stop_pool_monitoring(self: _DatabaseConnectionProtocol):
        self.monitor.stop_pool_monitoring()

    def get_correlation_id(self: _DatabaseConnectionProtocol) -> Optional[str]:
        return getattr(self._local, "correlation_id", None)

    def set_correlation_id(self: _DatabaseConnectionProtocol, correlation_id: str):
        self._local.correlation_id = correlation_id

    def _generate_correlation_id(self: _DatabaseConnectionProtocol) -> str:
        return str(uuid.uuid4())
