import ssl
from typing import Any, Optional, Dict, List, Tuple
import time
from .base import BaseEngine
from .pool import ConnectionPool
import utils.logger as logger


_DRIVER_HINT = "pg8000"
_MISSING_DRIVER = (
    f"{_DRIVER_HINT} not installed. Please install with: pip install {_DRIVER_HINT}"
)


class DictRow(list):
    """A result row that behaves as both a sequence and a mapping.

    The rest of the codebase reads rows through *raw* cursors as well as
    through ``fetch_one``/``fetch_all``, and it relies on the row shape both
    previous drivers provided: ``sqlite3.Row`` and psycopg's ``DictRow``
    support positional access (``row[0]``), column access (``row["name"]``),
    ``row.keys()`` and ``dict(row)``.

    pg8000 returns bare tuples, which support none of the mapping forms, so
    the cursor adapter below wraps them in this type instead of teaching a
    dozen call sites (``dict(row)`` in voice/push/servers/profiles/antispam)
    about a driver detail.
    """

    __slots__ = ("_index",)

    def __init__(self, values: Any, columns: List[str]) -> None:
        super().__init__(values)
        # Last duplicate wins, matching how ``dict(zip(...))`` and the drivers
        # collapse repeated column names.
        object.__setattr__(self, "_index", {name: i for i, name in enumerate(columns)})

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return super().__getitem__(self._index[key])
        return super().__getitem__(key)

    def keys(self) -> List[str]:
        return list(self._index.keys())

    def values(self) -> List[Any]:
        return list(self)

    def items(self) -> List[Tuple[str, Any]]:
        return list(zip(self.keys(), self))

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except (KeyError, IndexError):
            return default


def _column_names(cursor: Any) -> List[str]:
    """Column names from a DB-API cursor, or [] when it returned no rows."""
    description = getattr(cursor, "description", None)
    if not description:
        return []
    return [column[0] for column in description]


def _is_integrity_violation(exc: BaseException) -> bool:
    """True when ``exc`` is a PostgreSQL integrity-constraint violation.

    SQLSTATE class 23 covers UNIQUE, NOT NULL, CHECK, FOREIGN KEY and
    exclusion violations. pg8000 attaches the server's structured error
    fields to the exception's first argument, so the code is read from there;
    the class-name check covers an already-normalised error.
    """
    args = getattr(exc, "args", None)
    payload = args[0] if args else None
    if isinstance(payload, dict):
        code = payload.get("C")
        if isinstance(code, str):
            return code.startswith("23")
    return type(exc).__name__ in ("IntegrityError", "UniqueViolation")


def _normalize_driver_error(exc: BaseException) -> None:
    """Re-raise a constraint violation as the DB-API ``IntegrityError``.

    pg8000's own ``IntegrityError`` docstring states the class is "not
    currently raised by pg8000": every SQL error — integrity violations
    included — surfaces as the base ``DatabaseError``. Callers written
    against DB-API 2.0, and against psycopg (the driver this adapter
    replaced), catch ``IntegrityError`` for UNIQUE/NOT NULL/CHECK/FOREIGN
    KEY failures; without normalisation that handling silently misses on
    PostgreSQL while working on SQLite. ``IntegrityError`` subclasses
    ``DatabaseError``, so handlers catching the base class are unaffected.

    Returns normally when ``exc`` is not an integrity violation, so callers
    can use it as a guard immediately before re-raising.
    """
    if not _is_integrity_violation(exc):
        return
    try:
        import pg8000.dbapi
    except ImportError:  # pragma: no cover - the connection needs the driver
        return
    raise pg8000.dbapi.IntegrityError(*exc.args) from exc


class _Pg8000Cursor:
    """DB-API cursor adapter that yields :class:`DictRow` instead of tuples.

    Everything except the fetch methods is delegated to the real cursor, so
    ``execute``/``executemany``/``rowcount``/``description`` behave normally.
    """

    __slots__ = ("_cursor",)

    def __init__(self, cursor: Any) -> None:
        object.__setattr__(self, "_cursor", cursor)

    def _wrap(self, row: Any) -> Any:
        if row is None or hasattr(row, "keys"):
            return row
        columns = _column_names(self._cursor)
        if not columns:
            return row
        return DictRow(row, columns)

    def execute(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Delegate, normalising constraint violations (see module helper)."""
        try:
            return self._cursor.execute(operation, *args, **kwargs)
        except Exception as e:
            _normalize_driver_error(e)
            raise

    def executemany(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        """Delegate, normalising constraint violations (see module helper)."""
        try:
            return self._cursor.executemany(operation, *args, **kwargs)
        except Exception as e:
            _normalize_driver_error(e)
            raise

    def fetchone(self) -> Any:
        return self._wrap(self._cursor.fetchone())

    def fetchall(self) -> List[Any]:
        columns = _column_names(self._cursor)
        return [
            row if row is None or hasattr(row, "keys") else DictRow(row, columns)
            for row in self._cursor.fetchall()
        ]

    def fetchmany(self, size: Any = None) -> List[Any]:
        rows = (
            self._cursor.fetchmany() if size is None else self._cursor.fetchmany(size)
        )
        columns = _column_names(self._cursor)
        return [
            row if row is None or hasattr(row, "keys") else DictRow(row, columns)
            for row in rows
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)

    def __iter__(self) -> Any:
        return (self._wrap(row) for row in iter(self._cursor))


class _Pg8000Connection:
    """DB-API adapter over a pg8000 connection.

    pg8000 exposes neither ``closed`` nor ``broken``, but the shared
    connection layer reads both when revalidating a pooled connection. They
    are derived here from the driver's own socket state instead of teaching
    the shared layer about a specific driver. ``cursor()`` returns the
    row-wrapping adapter above; everything else is delegated unchanged.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: Any) -> None:
        object.__setattr__(self, "_conn", conn)

    def cursor(self) -> _Pg8000Cursor:
        return _Pg8000Cursor(self._conn.cursor())

    @property
    def closed(self) -> bool:
        """True once pg8000 has torn the socket down (its ``close()`` path)."""
        usock = getattr(self._conn, "_usock", None)
        if usock is None:
            return True
        try:
            return usock.fileno() < 0
        except OSError:
            # Socket already closed underneath us.
            return True
        except Exception:
            return False

    @property
    def broken(self) -> bool:
        """True when the connection can no longer be used.

        pg8000 keeps no broken flag: a transport failure surfaces as
        ``InterfaceError("network error")`` and is retried by the execution
        layer, which closes the connection on the way out. A connection whose
        socket is gone is unusable, which is the case this probe covers.
        """
        return self.closed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _build_ssl_context(sslmode: str) -> Optional["ssl.SSLContext"]:
    """Map a libpq ``sslmode`` onto an :class:`ssl.SSLContext`.

    pg8000 takes a single ``ssl_context`` knob, so the libpq modes are
    projected onto it::

        disable / allow   -> plaintext
        prefer / require  -> TLS, certificate not verified
        verify-ca         -> TLS, CA verified, hostname not checked
        verify-full       -> TLS, full verification

    ``prefer`` matches libpq's historical behaviour (encrypt when the server
    supports it, without verifying the certificate unless a root CA
    is supplied externally); the plaintext fallback is handled by the caller.
    """
    mode = (sslmode or "prefer").lower()
    if mode in ("disable", "allow"):
        return None

    context = ssl.create_default_context()
    if mode in ("prefer", "require"):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        context.check_hostname = False
    # "verify-full" keeps create_default_context()'s CA + hostname checks.
    return context


class PostgresPool(ConnectionPool):
    """Connection pool for PostgreSQL (the shared driver-agnostic pool)."""

    def __init__(
        self,
        create_conn,
        min_size: int,
        max_size: int,
        idle_reclaim_seconds: float = 5.0,
    ):
        super().__init__(
            create_conn=create_conn,
            min_size=min_size,
            max_size=max_size,
            idle_reclaim_seconds=idle_reclaim_seconds,
            label="PostgreSQL",
        )


class PostgresEngine(BaseEngine):
    """PostgreSQL database engine implementation (pg8000).

    pg8000 is a pure-Python DB-API 2.0 driver under BSD-3-Clause; it replaces
    psycopg, whose wheels are LGPL-3.0-only. The app speaks the DB-API cursor
    interface (``execute``/``fetchone``/``fetchall``/``rowcount``) and emits
    ``%s`` placeholders, both of which pg8000 supports natively, so the rest
    of the data layer is unchanged.

    pg8000 has no ``dict_row`` factory (psycopg's did), so the cursor adapter
    above wraps rows in :class:`DictRow`, which reproduces the shape both
    previous drivers returned: positional access, column access, ``keys()``
    and ``dict(row)``.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._pool: Optional[PostgresPool] = None
        self._pool_config = config.get("connection_pool", {})

    def _sslmode(self) -> str:
        return str(self.config.get("postgres", {}).get("sslmode", "prefer"))

    def _connect_kwargs(self) -> Dict[str, Any]:
        """Build the pg8000 connection parameters.

        Passed as a mapping (never a DSN) so the password cannot leak into a
        log line. ``timeout`` is pg8000's socket connect timeout.

        Note pg8000 exposes only an on/off ``tcp_keepalive`` rather than
        libpq's fine-grained ``keepalives*``/``tcp_user_timeout`` knobs.
        """
        pg_config = self.config.get("postgres", {})
        return {
            "host": pg_config.get("host", "localhost"),
            "port": pg_config.get("port", 5432),
            "user": pg_config.get("user", "postgres"),
            "password": pg_config.get("password", ""),
            "database": pg_config.get("dbname", "plexichat"),
            "timeout": self._pool_config.get("connect_timeout", 10),
            "tcp_keepalive": True,
            "application_name": "plexichat",
        }

    def _create_single_connection(self) -> _Pg8000Connection:
        try:
            import pg8000.dbapi
        except ImportError:
            logger.error(_MISSING_DRIVER)
            raise ImportError(_MISSING_DRIVER)

        kwargs = self._connect_kwargs()
        mode = self._sslmode().lower()
        ssl_context = _build_ssl_context(mode)
        if ssl_context is not None:
            kwargs["ssl_context"] = ssl_context

        try:
            return _Pg8000Connection(pg8000.dbapi.connect(**kwargs))
        except Exception as e:
            # sslmode=prefer means "encrypt if the server can, otherwise
            # connect in the clear" — pg8000 never falls back on its own, so
            # a server without TLS (or an unusable certificate) is retried
            # without the SSL context rather than failing the pool.
            #
            # This fallback is ONLY valid for the opportunistic ``prefer``
            # mode. ``require``/``verify-ca``/``verify-full`` demand TLS, so a
            # handshake failure there must propagate: retrying in the clear
            # would send the password and every row unencrypted on a
            # connection the operator explicitly required to be encrypted.
            if mode == "prefer" and "ssl" in str(e).lower() and ssl_context is not None:
                logger.warning(
                    f"PostgreSQL TLS handshake failed ({e}); retrying without "
                    "encryption because sslmode=prefer"
                )
                kwargs.pop("ssl_context", None)
                return _Pg8000Connection(pg8000.dbapi.connect(**kwargs))
            raise

    def connect(self, pool: Optional[Any] = None) -> Any:
        if isinstance(pool, ConnectionPool):
            # Honor the configured ``connect_timeout`` so pool exhaustion
            # surfaces fast instead of blocking the caller.
            timeout = self._pool_config.get("connect_timeout", 30.0)
            return pool.get_connection(timeout=timeout)
        return self._create_single_connection()

    def create_pool(self, min_conn: int, max_conn: int) -> Any:
        connect_timeout = self._pool_config.get("connect_timeout", 10)
        logger.info(
            f"Creating PostgreSQL connection pool ({min_conn}-{max_conn}) "
            f"with timeout {connect_timeout}s"
        )
        start_time = time.time()
        try:
            # ``min_conn`` connections are opened eagerly by the pool, so an
            # unreachable database still fails pool creation here (as the
            # psycopg pool's synchronous open did) instead of surfacing later
            # on the first query.
            pool = PostgresPool(
                create_conn=self._create_single_connection,
                min_size=min_conn,
                max_size=max_conn,
                idle_reclaim_seconds=float(
                    self._pool_config.get("idle_reclaim_seconds", 5.0)
                ),
            )
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(
                f"PostgreSQL connection pool creation FAILED after {elapsed:.2f}s: {e}"
            )
            raise

        elapsed = time.time() - start_time
        logger.info(
            f"PostgreSQL connection pool created successfully in {elapsed:.2f}s"
        )
        self._pool = pool
        return pool

    def get_pool_stats(self, pool: Any) -> Dict[str, Any]:
        stats: Dict[str, Any]
        if isinstance(pool, ConnectionPool):
            stats = pool.get_stats()
        else:
            stats = {
                "active_connections": 0,
                "idle_connections": 0,
                "total_connections": 0,
                "max_connections": 0,
                "min_connections": 0,
                "utilization_percent": 0,
            }

        try:
            pg_config = self.config.get("postgres", {})
            stats["database_type"] = "postgres"
            stats["database_host"] = pg_config.get("host", "localhost")
            stats["database_port"] = pg_config.get("port", 5432)
            stats["database_name"] = pg_config.get("dbname", "plexichat")
        except Exception as e:
            logger.debug(f"Could not retrieve PostgreSQL stats: {e}")

        return stats

    def close_connection(
        self,
        conn: Any,
        pool: Optional[Any] = None,
        close_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not conn:
            return

        if isinstance(pool, ConnectionPool):
            should_close = close_params.get("close", False) if close_params else False
            pool.return_connection(conn, force_close=should_close)
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

    def get_upsert_query(
        self,
        table: str,
        columns: List[str],
        conflict_columns: List[str],
        update_columns: List[str],
    ) -> str:
        safe_cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        conflict_cols = ", ".join(conflict_columns)
        updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_columns])
        return f"INSERT INTO {table} ({safe_cols}) VALUES ({placeholders}) ON CONFLICT ({conflict_cols}) DO UPDATE SET {updates}"
