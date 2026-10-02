import importlib
import json
import dataclasses
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union, cast

import utils.config as config
import utils.logger as logger


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, set):
            return list(o)
        if not isinstance(o, type) and hasattr(o, "to_dict"):
            return o.to_dict()
        return super().default(o)


def encode_scalar(value: Any) -> Any:
    """Coerce a value into something GLIDE can actually send.

    GLIDE's FFI layer accepts only ``str``/``bytes`` as bulk-string
    arguments; anything else raises ``TypeError: Unsupported argument
    type: <class 'int'>``. ``ValkeyValue`` is declared as
    ``Union[str, bytes, int, float]`` and ``eval_lua`` takes arbitrary
    arguments, so ints and floats reach the driver in normal use.

    The in-memory double used by the unit suite accepts any type (it
    stringifies what it is given), which is why this only ever blew up
    against a real server. Normalising here keeps every call site on one
    code path: bytes pass through untouched, str/int/float/bool/str-like
    values become UTF-8 bytes.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, (str, int, float, bool)):
        return str(value).encode("utf-8")
    # Unknown types are left alone so the driver reports them as it always
    # did, rather than this helper inventing a string representation.
    return value


class ValkeyError(Exception):
    pass


class ValkeyConnectionError(ValkeyError):
    pass


class ValkeyOperationError(ValkeyError):
    pass


ValkeyValue = Union[str, bytes, int, float]


class ValkeyClientBase:
    """Valkey connection manager with dual sync/async connections.

    GLIDE ships two packages with the same symbol surface:

    * ``glide_sync`` -- every command is a blocking call, held in
      ``self._client``.
    * ``glide`` -- every command is a coroutine, held in ``self._aclient``.

    The client keeps **both** because the underlying handles are bound to
    the event loop (``glide``) or the calling thread (``glide_sync``) that
    created them: a single handle cannot serve the serving loop and a
    worker thread at once, and running a coroutine on a second loop to
    share one async handle raises on cross-loop use. Two handles keeps
    each caller on the client it can actually use.

    Sync methods (``get``, ``set``, ``eval_lua``, ...) use ``_client`` and
    are only safe off the event loop. Async methods (``aget``, ``aset``,
    ``aeval_lua``, ...) use ``_aclient`` and must be awaited. The async
    handle is opened lazily by :meth:`aconnect` so deployments that never
    touch an async path pay for only one connection.
    """

    def __init__(self):
        # The config block is canonically named "valkey" (see
        # src/config_defaults.py). "redis" is accepted as a backward-compatible
        # alias so older deployments and tests that used the old key keep
        # working.
        self.config = config.get("valkey") or config.get("redis") or {}
        self.enabled = self.config.get("enabled", False)
        self._client: Any = None
        self._aclient: Any = None
        self._pubsub_handle: Any = None
        self._connected = False
        self._aconnected = False

        self.host = self.config.get("host", "localhost")
        try:
            self.port = int(self.config.get("port", 6379))
        except (TypeError, ValueError):
            self.port = 6379
        self.password = self.config.get("password", "") or None
        try:
            self.db = int(self.config.get("db", 0))
        except (TypeError, ValueError):
            self.db = 0
        self.ssl = self.config.get("ssl", False)

        self.key_prefix = self.config.get("key_prefix", "plexichat:")

        ttl_config = self.config.get("ttl", {}) or {}
        try:
            self.ttl_session = int(ttl_config.get("session", 1800))
        except (TypeError, ValueError):
            self.ttl_session = 1800
        try:
            self.ttl_presence = int(ttl_config.get("presence", 300))
        except (TypeError, ValueError):
            self.ttl_presence = 300
        try:
            self.ttl_cache = int(ttl_config.get("cache", 60))
        except (TypeError, ValueError):
            self.ttl_cache = 60

        self.worker_id = "unknown"

        if self.enabled:
            logger.info(
                f"GLIDE client initialized (host={self.host}:{self.port}, ssl={self.ssl})"
            )
        else:
            logger.info("GLIDE client disabled in configuration")

    @staticmethod
    def _decode(value: Optional[bytes]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    @staticmethod
    def _decode_list(values: List[bytes]) -> List[str]:
        return [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in values]

    @staticmethod
    def _decode_set(values: Set[bytes]) -> Set[str]:
        return {v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in values}

    @staticmethod
    def _decode_dict(d: Dict[bytes, bytes]) -> Dict[str, str]:
        return {
            k.decode("utf-8") if isinstance(k, bytes) else str(k): (
                v.decode("utf-8") if isinstance(v, bytes) else v
            )
            for k, v in d.items()
        }

    @staticmethod
    def _decode_mget(values: List[Optional[bytes]]) -> List[Optional[str]]:
        result: List[Optional[str]] = []
        for v in values:
            if v is None:
                result.append(None)
            elif isinstance(v, bytes):
                result.append(v.decode("utf-8"))
            else:
                result.append(str(v))
        return result

    def _config_kwargs(self, glide_mod: Any) -> dict:
        """Build the GlideClientConfiguration kwargs for a GLIDE module.

        ``glide`` and ``glide_sync`` expose identically-named configuration
        symbols, so the same keyword arguments build either handle; only the
        module they are read from differs.
        """
        pool_config = self.config.get("connection_pool", {}) or {}
        try:
            timeout = int(pool_config.get("timeout", 5))
        except (TypeError, ValueError):
            timeout = 5

        kwargs: dict = {
            "addresses": [glide_mod.NodeAddress(self.host, self.port)],
            "use_tls": self.ssl,
            "lazy_connect": True,
            "request_timeout": timeout * 1000,
            "reconnect_strategy": glide_mod.BackoffStrategy(
                num_of_retries=3, factor=timeout * 1000, exponent_base=2
            ),
            "database_id": self.db,
        }

        if self.password:
            kwargs["credentials"] = glide_mod.ServerCredentials(self.password)

        return kwargs

    def _record_connection_failure(self, exc: Exception) -> None:
        """Record a connection failure as a system alert."""
        try:
            from src.utils.system_alerts import record_system_alert

            record_system_alert(
                "connection_failure",
                {"host": self.host, "port": self.port, "error": str(exc)},
                source="valkey",
                severity="error",
                target_path="#alerts",
            )
        except Exception:
            pass

    def connect(self) -> bool:
        if not self.enabled:
            logger.debug("GLIDE is disabled, skipping connection")
            return False

        try:
            glide_sync = cast(Any, importlib.import_module("glide_sync"))
        except ImportError:
            logger.error(
                "valkey-glide-sync package not installed. Install with: pip install valkey-glide-sync"
            )
            raise ImportError(
                "valkey-glide-sync is required for Valkey support. "
                "Install with: pip install valkey-glide-sync"
            )

        try:
            glide_config = glide_sync.GlideClientConfiguration(
                **self._config_kwargs(glide_sync)
            )
            self._client = glide_sync.GlideClient.create(glide_config)

            self._client.ping()
            self._connected = True
            logger.info(f"Connected to Valkey at {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Valkey: {e}")
            self._connected = False
            self._record_connection_failure(e)
            raise ValkeyConnectionError(f"Failed to connect to Valkey: {e}")

    async def aconnect(self) -> bool:
        """Open the async (``glide``) handle used by the ``a*`` methods.

        Separate from :meth:`connect` on purpose -- see the class docstring.
        A coroutine handle is bound to the running loop, so this must be
        awaited from the loop that will issue the commands.
        """
        if not self.enabled:
            logger.debug("GLIDE is disabled, skipping async connection")
            return False

        try:
            glide = cast(Any, importlib.import_module("glide"))
        except ImportError:
            logger.error(
                "valkey-glide package not installed. Install with: pip install valkey-glide"
            )
            raise ImportError(
                "valkey-glide is required for the async Valkey API. "
                "Install with: pip install valkey-glide"
            )

        try:
            glide_config = glide.GlideClientConfiguration(**self._config_kwargs(glide))
            self._aclient = await glide.GlideClient.create(glide_config)

            await self._aclient.ping()
            self._aconnected = True
            logger.info(f"Connected to Valkey (async) at {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Valkey (async): {e}")
            self._aconnected = False
            self._record_connection_failure(e)
            raise ValkeyConnectionError(f"Failed to connect to Valkey: {e}")

    def set_worker_id(self, worker_id: str) -> None:
        self.worker_id = worker_id
        logger.debug(f"GLIDE client worker ID set to: {worker_id}")

    def _ensure_connected(self):
        if not self.enabled:
            raise ValkeyOperationError("GLIDE is disabled in configuration")
        if not self._connected or not self._client:
            raise ValkeyConnectionError("GLIDE not connected. Call connect() first.")

    def _ensure_aconnected(self) -> Any:
        """Return the async handle, or raise if it was never opened."""
        if not self.enabled:
            raise ValkeyOperationError("GLIDE is disabled in configuration")
        if not self._aconnected or not self._aclient:
            raise ValkeyConnectionError(
                "GLIDE async client not connected. Call aconnect() first."
            )
        return self._aclient

    def _prefixed_key(self, key: str) -> str:
        if key.startswith(self.key_prefix):
            return key
        return f"{self.key_prefix}{key}"

    def _sanitize_key(self, key: str) -> str:
        sanitized = "".join(c for c in key if c.isprintable() and c not in "\n\r\t")
        return sanitized[:512]

    def eval_lua(
        self,
        script: str,
        keys: Optional[List[str]] = None,
        args: Optional[List[Any]] = None,
    ) -> Any:
        Script = cast(Any, importlib.import_module("glide_sync")).Script

        self._ensure_connected()
        client = self._client
        assert client is not None

        resolved_keys: List[str] = keys or []
        resolved_args: List[Any] = [encode_scalar(a) for a in (args or [])]
        prefixed_keys = [self._prefixed_key(k) for k in resolved_keys]

        try:
            script_obj = Script(script)
            return client.invoke_script(
                script_obj, keys=prefixed_keys, args=resolved_args
            )
        except Exception as e:
            logger.error(f"GLIDE EVAL failed: {e}")
            raise ValkeyOperationError(f"EVAL failed: {e}")

    async def aeval_lua(
        self,
        script: str,
        keys: Optional[List[str]] = None,
        args: Optional[List[Any]] = None,
    ) -> Any:
        """Async counterpart of :meth:`eval_lua`."""
        Script = cast(Any, importlib.import_module("glide")).Script

        aclient = self._ensure_aconnected()

        resolved_keys: List[str] = keys or []
        resolved_args: List[Any] = [encode_scalar(a) for a in (args or [])]
        prefixed_keys = [self._prefixed_key(k) for k in resolved_keys]

        try:
            script_obj = Script(script)
            return await aclient.invoke_script(
                script_obj, keys=prefixed_keys, args=resolved_args
            )
        except Exception as e:
            logger.error(f"GLIDE EVAL (async) failed: {e}")
            raise ValkeyOperationError(f"EVAL failed: {e}")

    def close(self) -> None:
        if self._pubsub_handle:
            try:
                self._pubsub_handle = None
            except Exception:
                pass

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass

        self._client = None
        self._connected = False
        logger.info("GLIDE connection closed")

    async def aclose(self) -> None:
        """Async counterpart of :meth:`close`.

        Closes only the async handle; the sync handle (if open) is left
        untouched so a mixed caller does not lose its connection.
        """
        if self._aclient:
            try:
                await self._aclient.close()
            except Exception:
                pass

        self._aclient = None
        self._aconnected = False
        logger.info("GLIDE async connection closed")

    def __enter__(self) -> "ValkeyClientBase":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> object:
        self.close()
        return False

    async def __aenter__(self) -> "ValkeyClientBase":
        await self.aconnect()
        return self

    async def __aexit__(
        self, exc_type: object, exc_val: object, exc_tb: object
    ) -> object:
        await self.aclose()
        return False

    def set(self, key: str, value: ValkeyValue, ttl: Optional[int] = None) -> bool:
        raise NotImplementedError

    def set_nx(self, key: str, value: ValkeyValue, ttl: Optional[int] = None) -> bool:
        raise NotImplementedError

    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError

    async def aset(
        self, key: str, value: ValkeyValue, ttl: Optional[int] = None
    ) -> bool:
        raise NotImplementedError

    async def aset_nx(
        self, key: str, value: ValkeyValue, ttl: Optional[int] = None
    ) -> bool:
        raise NotImplementedError

    async def aget(self, key: str) -> Optional[str]:
        raise NotImplementedError
