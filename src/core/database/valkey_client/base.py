import json
import dataclasses
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union

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


class ValkeyError(Exception):
    pass


class ValkeyConnectionError(ValkeyError):
    pass


class ValkeyOperationError(ValkeyError):
    pass


ValkeyValue = Union[str, bytes, int, float]


class ValkeyClientBase:
    def __init__(self):
        self.config = config.get("redis") or {}
        self.enabled = self.config.get("enabled", False)
        self._client: Any = None
        self._pubsub_handle: Any = None
        self._connected = False

        self.host = self.config.get("host", "localhost")
        self.port = self.config.get("port", 6379)
        self.password = self.config.get("password", "") or None
        self.db = self.config.get("db", 0)
        self.ssl = self.config.get("ssl", False)

        self.key_prefix = self.config.get("key_prefix", "plexichat:")

        ttl_config = self.config.get("ttl", {})
        self.ttl_session = ttl_config.get("session", 1800)
        self.ttl_presence = ttl_config.get("presence", 300)
        self.ttl_cache = ttl_config.get("cache", 60)

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

    def connect(self) -> bool:
        if not self.enabled:
            logger.debug("GLIDE is disabled, skipping connection")
            return False

        try:
            from glide_sync import (
                GlideClient,
                GlideClientConfiguration,
                NodeAddress,
                ServerCredentials,
                BackoffStrategy,
            )
        except ImportError:
            logger.error(
                "valkey-glide-sync package not installed. Install with: pip install valkey-glide-sync"
            )
            raise ImportError(
                "valkey-glide-sync is required for Valkey support. "
                "Install with: pip install valkey-glide-sync"
            )

        try:
            addresses = [NodeAddress(self.host, self.port)]

            kwargs: dict = {
                "addresses": addresses,
                "use_tls": self.ssl,
                "lazy_connect": True,
            }

            if self.password:
                kwargs["credentials"] = ServerCredentials(self.password)

            pool_config = self.config.get("connection_pool", {})
            timeout = pool_config.get("timeout", 5)
            kwargs["request_timeout"] = timeout * 1000

            kwargs["reconnect_strategy"] = BackoffStrategy(
                num_of_retries=3, factor=timeout * 1000, exponent_base=2
            )

            kwargs["database_id"] = self.db

            glide_config = GlideClientConfiguration(**kwargs)
            self._client = GlideClient.create(glide_config)

            self._client.ping()
            self._connected = True
            logger.info(f"Connected to Valkey at {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Valkey: {e}")
            self._connected = False
            raise ValkeyConnectionError(f"Failed to connect to Valkey: {e}")

    def set_worker_id(self, worker_id: str) -> None:
        self.worker_id = worker_id
        logger.debug(f"GLIDE client worker ID set to: {worker_id}")

    def _ensure_connected(self):
        if not self.enabled:
            raise ValkeyOperationError("GLIDE is disabled in configuration")
        if not self._connected or not self._client:
            raise ValkeyConnectionError("GLIDE not connected. Call connect() first.")

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
        from glide_sync import Script

        self._ensure_connected()
        client = self._client
        assert client is not None

        resolved_keys: List[str] = keys or []
        resolved_args: List[Any] = args or []
        prefixed_keys = [self._prefixed_key(k) for k in resolved_keys]

        try:
            script_obj = Script(script)
            return client.invoke_script(
                script_obj, keys=prefixed_keys, args=resolved_args
            )
        except Exception as e:
            logger.error(f"GLIDE EVAL failed: {e}")
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

    def __enter__(self) -> "ValkeyClientBase":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> object:
        self.close()
        return False

    def set(self, key: str, value: ValkeyValue, ttl: Optional[int] = None) -> bool:
        raise NotImplementedError

    def get(self, key: str) -> Optional[str]:
        raise NotImplementedError
