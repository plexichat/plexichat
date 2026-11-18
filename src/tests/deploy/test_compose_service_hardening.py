"""Static healthcheck/dependency invariants for ``docker-compose.yml``.

The compose file is the canonical deployment topology. These tests parse it
with PyYAML (no daemon, no build) and pin the readiness guarantees that keep a
scaled-out deployment from starting a consumer before its stateful dependency
is actually usable. They fail closed if a healthcheck is dropped or weakened,
or if a dependent service is reverted to a bare ``service_started`` gate.

Note: these are structural checks. They cannot prove that ``curl``/``nc`` are
installed inside a third-party image; they pin the compose-side contract that
was hardened here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Services that keep on-disk state and therefore must expose a readiness probe.
STATEFUL_SERVICES = ("db", "valkey", "rustfs", "clamav", "meilisearch", "elasticsearch")

# One-shot bootstrappers must never restart (a looping init job would make any
# service waiting on `service_completed_successfully` block forever).
ONE_SHOT_SERVICES = ("rustfs-perms", "rustfs-init", "cert-init")

# Long-running daemons must come back after a transient failure.
DAEMON_SERVICES = (
    "db",
    "valkey",
    "rustfs",
    "clamav",
    "clamav-unofficial-sigs",
    "backend",
    "client",
    "backup",
    "shieldstral",
    "meilisearch",
    "elasticsearch",
)


@pytest.fixture(scope="module")
def services() -> dict[str, dict[str, Any]]:
    assert COMPOSE.is_file(), f"missing canonical compose file: {COMPOSE}"
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data.get("services"), dict)
    return data["services"]


def _condition(depends_on: Any, name: str) -> str:
    """Return the dependency condition for ``name`` ("service_started" in short form)."""
    assert isinstance(depends_on, dict), f"expected long-form depends_on, got {depends_on!r}"
    assert name in depends_on, f"missing dependency on {name}: {depends_on!r}"
    entry = depends_on[name]
    if isinstance(entry, dict):
        return str(entry.get("condition", "service_started"))
    return "service_started"


def _healthcheck_command(services: dict[str, dict[str, Any]], name: str) -> str:
    healthcheck = services[name].get("healthcheck")
    assert isinstance(healthcheck, dict), f"{name} has no healthcheck"
    test = healthcheck.get("test")
    assert isinstance(test, list) and len(test) >= 2, f"{name} has a malformed healthcheck test"
    assert test[0] in ("CMD", "CMD-SHELL"), f"{name} healthcheck must run a shell/command"
    command = str(test[1]).strip()
    assert command and command.lower() != "true", f"{name} healthcheck is a no-op"
    # Pin the tuning fields too: a missing timeout/retries can hang a rollout.
    for key in ("interval", "timeout", "retries"):
        assert key in healthcheck, f"{name} healthcheck is missing {key}"
    return command


def test_every_stateful_service_has_a_real_healthcheck(
    services: dict[str, dict[str, Any]]
) -> None:
    for name in STATEFUL_SERVICES:
        assert name in services, f"stateful service missing from compose: {name}"
        _healthcheck_command(services, name)


def test_backend_waits_for_its_stateful_dependencies(
    services: dict[str, dict[str, Any]]
) -> None:
    deps = services["backend"].get("depends_on")
    for name in ("db", "valkey", "clamav"):
        assert _condition(deps, name) == "service_healthy", (
            f"backend must wait for {name} to be healthy before serving traffic"
        )
    assert _condition(deps, "rustfs-init") == "service_completed_successfully", (
        "backend must wait for the one-shot S3 bucket init to finish"
    )


def test_bootstrap_jobs_wait_on_the_right_service_state(
    services: dict[str, dict[str, Any]]
) -> None:
    assert _condition(services["rustfs"]["depends_on"], "rustfs-perms") == (
        "service_completed_successfully"
    ), "rustfs needs the volume chown job to finish before it starts"
    assert _condition(services["rustfs-init"]["depends_on"], "rustfs") == (
        "service_healthy"
    ), "the bucket init needs a live, ready RustFS endpoint"
    assert _condition(services["backup"]["depends_on"], "db") == "service_healthy", (
        "a backup must not start against an unready database"
    )
    assert _condition(services["client"]["depends_on"], "cert-init") == (
        "service_completed_successfully"
    ), "nginx must not race certificate generation"


def test_clamav_signature_updater_waits_for_clamd_healthy(
    services: dict[str, dict[str, Any]]
) -> None:
    """The updater mutates the shared clamav_db volume; gate on real readiness."""
    assert _condition(services["clamav-unofficial-sigs"]["depends_on"], "clamav") == (
        "service_healthy"
    ), "signature updater must not write into a half-initialized clamav DB volume"


def test_meilisearch_healthcheck_asserts_available_status(
    services: dict[str, dict[str, Any]]
) -> None:
    command = _healthcheck_command(services, "meilisearch")
    assert "/health" in command
    assert "available" in command, "Meili reports status=available, not ok"
    assert "grep -q ok" not in command, (
        "the old `grep -q ok` never matched Meili's payload and only passed via a "
        "bare curl fallback that did not validate readiness"
    )


def test_elasticsearch_healthcheck_accepts_yellow_single_node(
    services: dict[str, dict[str, Any]]
) -> None:
    command = _healthcheck_command(services, "elasticsearch")
    assert "_cluster/health" in command
    assert "green|yellow" in command, (
        "a single-node cluster is YELLOW with default replicas; requiring only "
        "green made this check fall through to a weak root-endpoint grep"
    )
    assert "grep -q plexichat" not in command, (
        "the root-endpoint cluster-name grep must not be the readiness signal"
    )


def test_restart_policies_match_service_lifetime(
    services: dict[str, dict[str, Any]]
) -> None:
    for name in ONE_SHOT_SERVICES:
        assert services[name].get("restart") == "no", (
            f"one-shot job {name} must not restart (would block dependents)"
        )
    for name in DAEMON_SERVICES:
        assert services[name].get("restart") == "unless-stopped", (
            f"daemon {name} must restart automatically"
        )


def test_optional_heavy_services_declare_memory_limits(
    services: dict[str, dict[str, Any]]
) -> None:
    for name in ("shieldstral", "meilisearch", "elasticsearch"):
        limits = services[name].get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory"), f"{name} must declare a memory limit"


def test_scaling_guidance_is_preserved() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "--scale backend" in text, "compose must keep the horizontal-scaling note"
    assert "30000-30100/udp" in text, "ports caveat for scaled backend must stay accurate"
