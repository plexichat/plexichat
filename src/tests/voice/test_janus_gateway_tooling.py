"""The opt-in Janus gateway tooling must stay runnable and pinned.

The live-gateway tests in ``test_sfu_janus_gateway.py`` are skipped without a
gateway, so nothing else in the suite would notice the tooling that makes them
runnable rotting: an unpinned image tag, an API address the readiness probe
cannot reach, or a port that no longer matches the server's default
``janus_url``. These checks are cheap and they fail loudly.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
JANUS_DIR = REPO_ROOT / "docker" / "janus"
COMPOSE = JANUS_DIR / "docker-compose.janus.yml"
RUNNER = JANUS_DIR / "run-janus-gateway.sh"
README = JANUS_DIR / "README.md"
TEST_MODULE = REPO_ROOT / "src" / "tests" / "voice" / "test_sfu_janus_gateway.py"
INITIALIZER = REPO_ROOT / "src" / "server" / "initializer.py"

# The port the compose file must publish: the adapter's configured default.
DEFAULT_JANUS_PORT = "8088"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_the_compose_file_exists_and_parses(compose: dict) -> None:
    assert "services" in compose
    assert "janus" in compose["services"]


def test_the_gateway_image_is_pinned_to_a_release(compose: dict) -> None:
    """``latest`` would make the tooling silently drift under the tests."""
    image = compose["services"]["janus"]["image"]
    assert ":" in image, image
    tag = image.rsplit(":", 1)[1]
    assert tag not in {"latest", "master"}, image


def test_the_gateway_publishes_the_port_the_server_defaults_to(compose: dict) -> None:
    ports = compose["services"]["janus"]["ports"]
    assert any(str(port).startswith(DEFAULT_JANUS_PORT) for port in ports), ports
    assert f"localhost:{DEFAULT_JANUS_PORT}/janus" in INITIALIZER.read_text(
        encoding="utf-8"
    )


def test_the_runner_is_executable_and_handles_both_directions() -> None:
    assert os.access(RUNNER, os.X_OK), "run-janus-gateway.sh must be executable"
    script = RUNNER.read_text(encoding="utf-8")
    assert "PLEXICHAT_JANUS_URL" in script
    assert "down" in script and "up" in script


def test_the_runner_checks_the_videoroom_plugin() -> None:
    """Readiness must mean the plugin answered, not that a container started."""
    script = RUNNER.read_text(encoding="utf-8")
    assert "janus.plugin.videoroom" in script
    assert '"janus": "info"' in script


def test_the_readme_names_the_environment_the_tests_read() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "PLEXICHAT_JANUS_URL" in readme
    assert "test_sfu_janus_gateway.py" in readme


def test_the_test_module_points_at_the_tooling() -> None:
    """A skipped test must be able to find the script that unskips it."""
    text = TEST_MODULE.read_text(encoding="utf-8")
    assert "docker/janus/run-janus-gateway.sh" in text
