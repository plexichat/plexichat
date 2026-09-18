"""
Valkey Docker Fixtures for Testing.

The rest of the Valkey coverage runs against ``FakeGlideClient`` (see
``src/tests/test_valkey.py``), which reproduces the GLIDE *API* but not the
server's behaviour. This module supplies the other half: a real Valkey
instance in a throwaway container, plus fixtures that hand tests a genuinely
connected :class:`~src.core.database.valkey_client.ValkeyClient`.

Features:
- Automatic Docker container lifecycle management
- testcontainers-python preferred, docker SDK as fallback
- Fallback to an already-running local Valkey
- Configuration discovery from environment variables
- Automatic cleanup on test completion

Usage:
    def test_with_valkey(valkey_client):
        valkey_client.set("k", "v")
        assert valkey_client.get("k") == "v"

Environment Variables:
    VALKEY_HOST: Override default localhost
    VALKEY_PORT: Override default 6379
    VALKEY_PASSWORD: Password for the local instance
    VALKEY_IMAGE: Image to run (default valkey/valkey:8-alpine)
    USE_DOCKER: Force Docker usage (true/false)
"""

import os
import time
import logging
from typing import Any, Dict, Generator, Optional

import pytest
import pytest_asyncio

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "valkey/valkey:8-alpine"

# Test keys live under their own prefix so a fixture's ``flush_prefix`` can
# never touch anything else that happens to share the server.
TEST_KEY_PREFIX = "pytest:"


def _ping(host: str, port: int, password: str = "", timeout: int = 2) -> bool:
    """Return True when a GLIDE client can reach and PING the server.

    Uses the same driver the application uses, so "reachable" means
    reachable *for the code under test*, not merely that the TCP port
    accepts connections.
    """
    try:
        from glide_sync import (
            BackoffStrategy,
            GlideClient,
            GlideClientConfiguration,
            NodeAddress,
            ServerCredentials,
        )

        kwargs: Dict[str, Any] = {
            "addresses": [NodeAddress(host, port)],
            "lazy_connect": True,
            "request_timeout": timeout * 1000,
            "reconnect_strategy": BackoffStrategy(
                num_of_retries=1, factor=timeout * 1000, exponent_base=2
            ),
        }
        if password:
            kwargs["credentials"] = ServerCredentials(password)

        client = GlideClient.create(GlideClientConfiguration(**kwargs))
        try:
            return client.ping() == b"PONG"
        finally:
            client.close()
    except Exception as exc:  # any failure means "not reachable yet"
        logger.debug(f"Valkey probe {host}:{port} failed: {exc}")
        return False


class ValkeyDockerManager:
    """Manages a Valkey container (or a local instance) lifecycle."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: str = "",
        image: str = DEFAULT_IMAGE,
        use_docker: bool = True,
    ):
        self.host = host
        self.port = port
        self.password = password
        self.image = image
        self.use_docker = use_docker
        self.container_id: Optional[str] = None
        self.container = None

    def get_config(self) -> Dict[str, Any]:
        """Get the ``valkey`` config block pointing at this instance."""
        return {
            "valkey": {
                "enabled": True,
                "host": self.host,
                "port": self.port,
                "password": self.password,
                "db": 0,
                "ssl": False,
                "key_prefix": TEST_KEY_PREFIX,
                "connection_pool": {"timeout": 5, "max_connections": 10},
                "ttl": {"session": 1800, "presence": 300, "cache": 60},
            }
        }

    def start(self) -> bool:
        """Make a Valkey server available, preferring a container."""
        if not self.use_docker:
            logger.info("Docker disabled - expecting an already-running Valkey")
            return self._wait_for_valkey_or_false(wait=True)

        try:
            return self._start_with_testcontainers()
        except Exception as e:  # fall through to the next backend
            logger.debug(f"testcontainers unavailable ({e}), trying docker SDK")
            try:
                return self._start_with_docker_sdk()
            except Exception as e2:
                logger.warning(
                    f"Neither testcontainers nor docker SDK usable ({e2}); "
                    f"falling back to local Valkey at {self.host}:{self.port}"
                )
                return self._wait_for_valkey_or_false()

    def _start_with_testcontainers(self) -> bool:
        from testcontainers.core.container import DockerContainer

        logger.info(f"Starting Valkey container using testcontainers ({self.image})")
        container = (
            DockerContainer(self.image)
            .with_exposed_ports(6379)
            .with_bind_ports(self.port, 6379)
            .with_command("valkey-server --save '' --appendonly no")
        )
        container.start()

        self.container = container
        self.host = container.get_container_host_ip()
        self.port = int(container.get_exposed_port(6379))
        logger.info(f"Valkey container started: {self.host}:{self.port}")

        self._wait_for_valkey()
        return True

    def _start_with_docker_sdk(self) -> bool:
        import docker

        logger.info(f"Starting Valkey container using docker SDK ({self.image})")
        client = docker.from_env()

        try:
            client.images.get(self.image)
        except docker.errors.ImageNotFound:
            logger.info(f"Pulling Docker image: {self.image}")
            client.images.pull(self.image)

        self.container = client.containers.run(
            self.image,
            ["valkey-server", "--save", "", "--appendonly", "no"],
            detach=True,
            ports={"6379/tcp": self.port},
            name=f"pytest-valkey-{int(time.time())}",
            remove=True,
        )
        self.container_id = self.container.id
        logger.info(f"Valkey container started: {self.container_id}")

        self._wait_for_valkey()
        return True

    def _wait_for_valkey(self, timeout: int = 30) -> None:
        """Poll until the server answers PING, or raise ``TimeoutError``."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if _ping(self.host, self.port, self.password):
                logger.info("Valkey is ready for connections")
                return
            time.sleep(0.5)
        raise TimeoutError(f"Valkey not ready after {timeout}s")

    def _wait_for_valkey_or_false(self, wait: bool = False) -> bool:
        """Look for an externally-provided server, reporting absence.

        ``wait`` is set only when the caller explicitly asked to attach to a
        server instead of spawning one (``USE_DOCKER=false``). That is the CI
        case, where the *service* container is still starting when the job
        begins: a one-shot probe would report "not available" and let the
        Valkey suite skip -- green without testing anything. The default path
        probes once so a developer without Docker is not delayed.
        """
        if not wait:
            return _ping(self.host, self.port, self.password)
        try:
            self._wait_for_valkey(timeout=int(os.getenv("VALKEY_WAIT_TIMEOUT", "30")))
            return True
        except TimeoutError:
            return False

    def stop(self) -> None:
        """Stop and remove the container, if this manager started one."""
        if self.container is None:
            return

        try:
            if hasattr(self.container, "stop"):
                # testcontainers container
                logger.info("Stopping Valkey container (testcontainers)")
                self.container.stop()
            else:
                # docker SDK container
                logger.info(f"Stopping Valkey container: {self.container_id}")
                self.container.stop(timeout=10)
                self.container.remove()
        except Exception as e:
            logger.error(f"Error stopping container: {e}")
        finally:
            self.container = None
            self.container_id = None


# Global manager instance
_valkey_manager: Optional[ValkeyDockerManager] = None


def get_valkey_manager() -> ValkeyDockerManager:
    """Get or create the global Valkey Docker manager."""
    global _valkey_manager

    if _valkey_manager is None:
        _valkey_manager = ValkeyDockerManager(
            host=os.getenv("VALKEY_HOST", "localhost"),
            port=int(os.getenv("VALKEY_PORT", "6379")),
            password=os.getenv("VALKEY_PASSWORD", ""),
            image=os.getenv("VALKEY_IMAGE", DEFAULT_IMAGE),
            use_docker=os.getenv("USE_DOCKER", "true").lower() == "true",
        )

    return _valkey_manager


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def valkey_manager() -> Generator[ValkeyDockerManager, None, None]:
    """Session-scoped real Valkey instance; skips when none is available."""
    manager = get_valkey_manager()

    if not manager.start():
        pytest.skip("Valkey not available")

    yield manager

    manager.stop()


@pytest.fixture(scope="session")
def valkey_config(valkey_manager: ValkeyDockerManager) -> Dict[str, Any]:
    """The ``valkey`` config block pointing at the live instance."""
    return valkey_manager.get_config()


def _bind_valkey_config(valkey_config: Dict[str, Any]) -> Dict[str, Any]:
    """Point the global config at the live instance and return the old block.

    The autouse ``_isolate_global_config`` fixture restores the whole config
    at teardown, so only the process-wide client handle needs undoing here.
    """
    import utils.config as config

    previous = config.get("valkey")
    config.set("valkey", dict(valkey_config["valkey"]))
    return previous


@pytest.fixture
def valkey_client(valkey_config: Dict[str, Any]) -> Generator[Any, None, None]:
    """A real, connected :class:`ValkeyClient` bound to the test instance.

    The client is also installed as the process-wide default for the
    duration of the test, so cache/rate-limit helpers that reach for
    ``get_client()`` exercise the real server too. The keyspace is flushed
    before and after each test, so tests see a clean, prefixed keyspace.
    """
    from src.core.database import valkey_client as valkey_module
    from src.core.database.valkey_client import ValkeyClient, ValkeyConnectionError

    previous_default = valkey_module._default_client
    _bind_valkey_config(valkey_config)

    client = ValkeyClient()
    try:
        client.connect()
    except ValkeyConnectionError as e:
        pytest.skip(f"Could not connect to Valkey: {e}")

    valkey_module._default_client = client
    client.flush_prefix()

    yield client

    try:
        client.flush_prefix()
        client.close()
    finally:
        valkey_module._default_client = previous_default


@pytest_asyncio.fixture
async def async_valkey_client(valkey_config: Dict[str, Any]) -> Any:
    """A real client with the event-loop (``glide``) handle open.

    ``aconnect`` must be awaited from the loop that will use it, so this is
    an async fixture rather than a wrapper around :func:`valkey_client`.
    """
    from src.core.database import valkey_client as valkey_module
    from src.core.database.valkey_client import ValkeyClient, ValkeyConnectionError

    previous_default = valkey_module._default_client
    _bind_valkey_config(valkey_config)

    client = ValkeyClient()
    try:
        await client.aconnect()
    except ValkeyConnectionError as e:
        pytest.skip(f"Could not connect to Valkey: {e}")

    valkey_module._default_client = client
    await client.aflush_prefix()

    yield client

    try:
        await client.aflush_prefix()
    finally:
        await client.aclose()
        client.close()
        valkey_module._default_client = previous_default
