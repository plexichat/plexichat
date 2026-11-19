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

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
JANUS_COMPOSE = REPO_ROOT / "docker/janus/docker-compose.janus.yml"
DIGEST_LOCK = REPO_ROOT / "image-digests.lock.json"
IMAGE_LINE_RE = re.compile(r"(?m)^\s*image:\s*(\S+)\s*$")

# Every third-party image ref in the compose file, pinned to the manifest
# digest re-resolved from its registry. Changing one requires re-verifying the
# digest online and updating image-digests.lock.json in the same change.
EXPECTED_PINS = {
    "db": "postgres:18-alpine@sha256:b6972e1115a4ff8eba5d7f7d19184926d1e16d7d1136ae1e1a7ebf286da0f015",
    "valkey": "valkey/valkey:9.1.1-alpine@sha256:15568b9cb7eb67f4aed4de018c23f13d344e0e6437b31fe8fb8823dc81ebb3a9",
    "rustfs-perms": "alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf",
    "rustfs": "rustfs/rustfs:1.0.0@sha256:8cc9801755448b71a786705ce76692c77e14936cccd87cf2fc31842e58f4d1ff",
    "rustfs-init": "rustfs/rc:v0.1.30@sha256:1296510ec27800668d26a8bdca6251e587086187a9368c7b80134a169e8a3bb8",
    "cert-init": "alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf",
    "clamav": "clamav/clamav:1.5.4@sha256:7054e3f34308c384ea43cd786ea17abf899bd44af40af9f1b099853d7a5c214e",
    "clamav-unofficial-sigs": "ghcr.io/extremeshok/clamav-unofficial-sigs:8.0.0@sha256:7503b665d6d8a539bc493d2fe1b4aa4b1c3623e1e05c2e6f699c02c87827695c",
    "meilisearch": "getmeili/meilisearch:v1.15@sha256:fe500cf9cca05cb9f027981583f28eccf17d35d94499c1f8b7b844e7418152fc",
    "elasticsearch": "docker.elastic.co/elasticsearch/elasticsearch:8.19.0@sha256:e1e66bfabae0fd03a0a36651a9bb198e7f061e0c99f457a6203b116e053e9cdb",
}
# The AI profile's operator-overridable default must still carry a valid pin.
LLAMA_DEFAULT_PIN = (
    "ghcr.io/ggml-org/llama.cpp:server-b12464@sha256:"
    "5b43086e3ba7ea5794d72904654f6cd41224a9b1c6ac0398f4940ee380d30f23"
)

# Tags that were superseded during the digest-pinning pass.
STALE_TAGS = ("1.0.0-rc.3", "v1.12", "8.15.0", "server-b10614")

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
    assert isinstance(depends_on, dict), (
        f"expected long-form depends_on, got {depends_on!r}"
    )
    assert name in depends_on, f"missing dependency on {name}: {depends_on!r}"
    entry = depends_on[name]
    if isinstance(entry, dict):
        return str(entry.get("condition", "service_started"))
    return "service_started"


def _healthcheck_command(services: dict[str, dict[str, Any]], name: str) -> str:
    healthcheck = services[name].get("healthcheck")
    assert isinstance(healthcheck, dict), f"{name} has no healthcheck"
    test = healthcheck.get("test")
    assert isinstance(test, list) and len(test) >= 2, (
        f"{name} has a malformed healthcheck test"
    )
    assert test[0] in ("CMD", "CMD-SHELL"), (
        f"{name} healthcheck must run a shell/command"
    )
    command = str(test[1]).strip()
    assert command and command.lower() != "true", f"{name} healthcheck is a no-op"
    # Pin the tuning fields too: a missing timeout/retries can hang a rollout.
    for key in ("interval", "timeout", "retries"):
        assert key in healthcheck, f"{name} healthcheck is missing {key}"
    return command


def test_every_stateful_service_has_a_real_healthcheck(
    services: dict[str, dict[str, Any]],
) -> None:
    for name in STATEFUL_SERVICES:
        assert name in services, f"stateful service missing from compose: {name}"
        _healthcheck_command(services, name)


def test_backend_waits_for_its_stateful_dependencies(
    services: dict[str, dict[str, Any]],
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
    services: dict[str, dict[str, Any]],
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
    services: dict[str, dict[str, Any]],
) -> None:
    """The updater mutates the shared clamav_db volume; gate on real readiness."""
    assert _condition(services["clamav-unofficial-sigs"]["depends_on"], "clamav") == (
        "service_healthy"
    ), "signature updater must not write into a half-initialized clamav DB volume"


def test_meilisearch_healthcheck_asserts_available_status(
    services: dict[str, dict[str, Any]],
) -> None:
    command = _healthcheck_command(services, "meilisearch")
    assert "/health" in command
    assert "available" in command, "Meili reports status=available, not ok"
    assert "grep -q ok" not in command, (
        "the old `grep -q ok` never matched Meili's payload and only passed via a "
        "bare curl fallback that did not validate readiness"
    )


def test_elasticsearch_healthcheck_accepts_yellow_single_node(
    services: dict[str, dict[str, Any]],
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
    services: dict[str, dict[str, Any]],
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
    services: dict[str, dict[str, Any]],
) -> None:
    for name in ("shieldstral", "meilisearch", "elasticsearch"):
        limits = services[name].get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory"), f"{name} must declare a memory limit"


def test_scaling_guidance_is_preserved() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "--scale backend" in text, "compose must keep the horizontal-scaling note"
    assert "30000-30100/udp" in text, (
        "ports caveat for scaled backend must stay accurate"
    )


def test_every_service_drops_new_privileges(
    services: dict[str, dict[str, Any]],
) -> None:
    """No container may gain privileges via setuid/setgid or file capabilities.

    This is a kernel-level backstop: even if a setuid helper or a capability-
    bearing file is introduced into an image, ``no_new_privs`` makes execve
    fail closed instead of handing root to a compromised process. Privilege
    *drops* performed by image entrypoints (gosu/su-exec) are unaffected.
    """
    assert services, "expected services in the canonical compose file"
    for name, spec in services.items():
        opts = spec.get("security_opt", [])
        assert isinstance(opts, list), f"{name} has a malformed security_opt: {opts!r}"
        assert any(
            str(opt).replace(" ", "") == "no-new-privileges:true" for opt in opts
        ), f"{name} is missing security_opt: no-new-privileges:true"


def test_backend_drops_capabilities_and_reaps_children(
    services: dict[str, dict[str, Any]],
) -> None:
    """The internet-facing, non-root backend needs no Linux capabilities."""
    backend = services["backend"]
    dropped = backend.get("cap_drop", [])
    assert isinstance(dropped, list) and "ALL" in dropped, (
        "backend must drop all Linux capabilities (it runs as UID 10001 and "
        "binds only unprivileged ports)"
    )
    assert backend.get("init") is True, (
        "backend must run under an init process so orphaned ffmpeg/websocket "
        "children are reaped instead of lingering as zombies"
    )


def test_janus_dev_api_is_bound_to_loopback() -> None:
    """The unauthenticated dev gateway must never listen on 0.0.0.0."""
    assert JANUS_COMPOSE.is_file(), f"missing Janus compose file: {JANUS_COMPOSE}"
    data = yaml.safe_load(JANUS_COMPOSE.read_text(encoding="utf-8"))
    ports = data["services"]["janus"].get("ports") or []
    assert ports, "Janus compose must publish its API ports for the adapter tests"
    for entry in ports:
        # Compose publishes "host:container"; a bare port means 0.0.0.0.
        host = str(entry).split(":", 1)[0]
        assert host == "127.0.0.1", (
            f"Janus dev API port {entry!r} is not pinned to 127.0.0.1; an "
            "unauthenticated API must not be exposed on all host interfaces"
        )
    for line in JANUS_COMPOSE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("-") and ":" in stripped:
            assert "0.0.0.0" not in stripped, (
                f"Janus port binding must not use a 0.0.0.0 bind: {stripped}"
            )


def test_janus_dev_gateway_is_digest_pinned_and_unprivileged() -> None:
    """The dev gateway follows the main stack's pin/no-new-privs policy.

    A tag alone is mutable in the registry, so ``0.10.7`` could be replaced
    between pulls; pin the manifest digest. It is also an unauthenticated
    network listener, so it must carry the same setuid/capability backstop as
    every service in the main compose file.
    """
    data = yaml.safe_load(JANUS_COMPOSE.read_text(encoding="utf-8"))
    janus = data["services"]["janus"]
    image = str(janus["image"])
    assert re.search(r"@sha256:[0-9a-f]{64}$", image), (
        f"Janus dev image must be digest-pinned, got {image!r}"
    )
    assert image.startswith("canyan/janus-gateway:0.10.7@"), (
        "unexpected Janus image ref; re-verify the digest before changing it"
    )
    opts = janus.get("security_opt", [])
    assert any(str(opt).replace(" ", "") == "no-new-privileges:true" for opt in opts), (
        "the unauthenticated Janus listener must deny new privileges"
    )


def test_third_party_images_are_pinned_to_verified_digests(
    services: dict[str, dict[str, Any]],
) -> None:
    for name, expected in EXPECTED_PINS.items():
        assert services[name]["image"] == expected, f"unexpected pin for {name}"
    llama = services["shieldstral"]["image"]
    assert LLAMA_DEFAULT_PIN in llama, (
        "AI profile default must carry a valid digest pin"
    )
    assert "server-b10614" not in llama, "the stale llama.cpp build tag must stay gone"


def test_no_third_party_image_is_tag_only_or_floating() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    refs = [m.group(1) for m in IMAGE_LINE_RE.finditer(text)]
    assert refs, "expected image lines in the compose file"
    for ref in refs:
        if "${CI_REGISTRY_IMAGE" in ref:
            continue  # first-party, require-pinned via ${BACKEND_VERSION:?...}
        assert "@sha256:" in ref, f"third-party image is not digest-pinned: {ref}"
        assert not ref.endswith(":latest"), f"floating third-party image: {ref}"
        for stale in STALE_TAGS:
            assert stale not in ref, f"stale tag {stale} still referenced: {ref}"


def test_digest_lock_matches_every_pinned_image() -> None:
    assert DIGEST_LOCK.is_file(), f"missing digest lock: {DIGEST_LOCK}"
    lock = json.loads(DIGEST_LOCK.read_text(encoding="utf-8"))
    assert lock.get("format") == "plexichat/image-digests-lock/v1"
    images = lock["images"]
    expected_refs = set(EXPECTED_PINS.values()) | {LLAMA_DEFAULT_PIN}
    assert set(images) == expected_refs, (
        f"digest lock refs drifted from compose: missing={sorted(expected_refs - set(images))}, "
        f"extra={sorted(set(images) - expected_refs)}"
    )
    for ref, entry in images.items():
        assert entry["reference"] == ref, (
            f"lock entry has a mismatched reference: {ref}"
        )
        assert entry["tag"], f"lock entry is missing its tag: {ref}"
        digest = "@sha256:" + ref.rsplit("@sha256:", 1)[1]
        assert entry["digest"] == digest, f"lock digest does not match pin: {ref}"
        assert re.fullmatch(r"@sha256:[0-9a-f]{64}", digest), f"malformed digest: {ref}"


def test_supply_chain_policy_markers_are_preserved() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    for marker in (
        "ALLOW_DEV_COMPOSE",
        "ALLOW_DOCKERHUB_FALLBACK",
        "ALLOW_UNPINNED_IMAGES",
    ):
        assert marker in text, f"pinning policy marker was removed: {marker}"


# --- Horizontal-scaling / resource-budget invariants ------------------------

# Every long-lived daemon that a scaled backend fleet shares must be bounded:
# without per-container ceilings, N hungry workers can OOM the host through a
# dependency instead of failing in one place.
RESOURCE_BOUNDED_SERVICES = (
    "db",
    "valkey",
    "rustfs",
    "clamav",
    "clamav-unofficial-sigs",
    "backend",
    "client",
    "backup",
)


def _resource_limits(services: dict[str, dict[str, Any]], name: str) -> dict[str, Any]:
    deploy = services[name].get("deploy")
    assert isinstance(deploy, dict), f"{name} has no deploy block for resource limits"
    resources = deploy.get("resources")
    assert isinstance(resources, dict), f"{name} has no deploy.resources"
    limits = resources.get("limits")
    assert isinstance(limits, dict), f"{name} has no deploy.resources.limits"
    return limits


def _memory_bytes(value: Any) -> int:
    """Parse a Compose memory quantity, resolving ``${VAR:-default}`` to its default."""
    text = re.sub(r"\$\{[^}]*?:-([^}]*)\}", r"\1", str(value)).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?", text, re.IGNORECASE)
    assert match, f"unparseable memory quantity: {value!r}"
    scale = {"": 1, "k": 2**10, "m": 2**20, "g": 2**30, "t": 2**40}[
        match.group(2).lower()
    ]
    return int(float(match.group(1)) * scale)


def _duration_seconds(value: Any) -> float:
    """Parse a Compose duration such as ``20s``/``5m``/``1m0s`` into seconds."""
    parts = re.findall(r"(\d+(?:\.\d+)?)([smh])", str(value), re.IGNORECASE)
    assert parts, f"unparseable duration: {value!r}"
    factors = {"s": 1, "m": 60, "h": 3600}
    return sum(float(number) * factors[unit.lower()] for number, unit in parts)


def test_scalable_services_declare_bounded_cpu_and_memory(
    services: dict[str, dict[str, Any]],
) -> None:
    for name in RESOURCE_BOUNDED_SERVICES:
        limits = _resource_limits(services, name)
        assert limits.get("cpus"), f"{name} must cap CPU to keep replicas fair"
        assert limits.get("memory"), (
            f"{name} must cap memory to bound a runaway replica"
        )
        assert _memory_bytes(limits["memory"]) >= 256 * 2**20, (
            f"{name} memory limit is too small to start, let alone serve load"
        )
    # ClamAV loads ~1.2 GB of signatures and peaks around ~2.4 GB on reload;
    # the 4G ceiling documented in docker/README.md must survive refactors.
    assert _memory_bytes(_resource_limits(services, "clamav")["memory"]) >= 3 * 2**30, (
        "clamav's memory limit dropped below its documented reload working set"
    )


def test_optional_heavy_services_declare_cpu_limits(
    services: dict[str, dict[str, Any]],
) -> None:
    for name in ("shieldstral", "meilisearch", "elasticsearch"):
        assert _resource_limits(services, name).get("cpus"), (
            f"{name} must declare a CPU limit alongside its memory limit"
        )


def test_every_long_running_daemon_declares_resource_limits(
    services: dict[str, dict[str, Any]]
) -> None:
    """Every daemon must be capped; deriving the set keeps this fail-closed.

    The earlier resource pass capped the headline services but missed the
    ClamAV signature updater, which is a ``restart: unless-stopped`` daemon
    that decompresses third-party signature archives into a shared volume.
    An unbounded one can OOM the host or starve clamd itself. One-shot jobs
    (rustfs-perms/rustfs-init/cert-init) are intentionally exempt.
    """
    for name in DAEMON_SERVICES:
        limits = _resource_limits(services, name)
        assert limits.get("cpus"), f"daemon {name} must cap CPU"
        assert limits.get("memory"), f"daemon {name} must cap memory"


def test_shared_env_is_scoped_to_services_that_need_it(
    services: dict[str, dict[str, Any]]
) -> None:
    """Optional third-party services must not inherit every shared secret.

    ``.env`` holds Postgres/Valkey/S3/restic credentials. Services that only
    need a public setting or one interpolated value must not receive the whole
    file in their process environment; that is the same F8 rule the perimeter
    ``client`` already follows. Compose still interpolates ``${VAR}`` entries
    in ``environment:`` from .env, so dropping ``env_file`` loses no config.
    """
    no_shared_env = (
        "client",  # perimeter service (F8)
        "clamav",
        "clamav-unofficial-sigs",
        "shieldstral",
        "meilisearch",  # optional third-party, master key passed explicitly
        "elasticsearch",  # optional third-party, no shared secret needed
        "rustfs-perms",  # one-shot chown job
        "cert-init",  # one-shot self-signed cert job
    )
    for name in no_shared_env:
        assert not services[name].get("env_file"), (
            f"{name} must not receive the shared .env (all secrets) in its "
            "process environment; pass only the variables it needs"
        )
    # Services that genuinely consume many .env values must keep it, so a
    # future "simplification" cannot silently break DB/S3/backup auth.
    for name in ("db", "valkey", "rustfs", "rustfs-init", "backend", "backup"):
        assert services[name].get("env_file"), (
            f"{name} relies on .env for runtime secrets; its env_file was removed"
        )


def test_backend_healthcheck_is_not_aggressive_under_load(
    services: dict[str, dict[str, Any]],
) -> None:
    """N warming replicas must not be marked unhealthy on one slow response."""
    healthcheck = services["backend"]["healthcheck"]
    assert _duration_seconds(healthcheck["interval"]) >= 20
    assert _duration_seconds(healthcheck["timeout"]) >= 10
    assert int(healthcheck["retries"]) >= 5
    assert _duration_seconds(healthcheck["start_period"]) >= 20


def test_backend_swarm_replicas_use_a_separate_variable(
    services: dict[str, dict[str, Any]],
) -> None:
    """The pool-budget hint must not silently spawn N port-bound containers."""
    replicas = str(services["backend"]["deploy"]["replicas"])
    assert replicas == "${BACKEND_SWARM_REPLICAS:-1}", (
        "deploy.replicas must default to one and use BACKEND_SWARM_REPLICAS, "
        "not the BACKEND_REPLICAS env var passed into each container for "
        "connection-pool budgeting"
    )


def test_backend_rolling_update_is_safe_for_a_scaled_fleet(
    services: dict[str, dict[str, Any]],
) -> None:
    update = services["backend"]["deploy"].get("update_config")
    assert isinstance(update, dict), (
        "backend must declare a Swarm rolling-update policy"
    )
    assert int(update.get("parallelism", 0)) == 1, "replace one worker at a time"
    assert update.get("order") == "start-first", (
        "keep the old worker until the new one is up"
    )
    assert update.get("failure_action") == "rollback", "a failed rollout must roll back"
    assert _duration_seconds(update.get("monitor", 0)) >= 60, (
        "failure monitoring must outlast the healthcheck's start period"
    )


def test_scaling_notes_cover_pool_budget_and_first_boot() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    for marker in (
        "BACKEND_REPLICAS=N",
        "media.signing_key",
        "BACKEND_SWARM_REPLICAS",
        "PER REPLICA",
    ):
        assert marker in text, f"scaling guidance lost its {marker!r} note"
