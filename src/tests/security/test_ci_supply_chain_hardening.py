"""Supply-chain regressions for the committed CI pipeline config.

Agent 55 F23: the pipeline used to commit a repository-wide
``PIP_INDEX_URL`` pointing at a plaintext ``http://`` mirror plus
``PIP_TRUSTED_HOST``. Because it applied to every job, an on-path attacker
could have injected arbitrary wheels into the build (including the
unhashed ``pip install pip-audit safety`` bootstrap), controlling the
dependency tree.

Agent 55 F22: the release/backup/cleanup jobs ran ``docker:27-dind`` with
``DOCKER_TLS_CERTDIR=""`` and ``DOCKER_HOST=tcp://docker:2375`` — an
unauthenticated TCP Docker socket that is root-equivalent for anything on
the job network.

These tests fail if either regression is reintroduced.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CI_PATH = _REPO_ROOT / ".gitlab-ci.yml"


def _load_ci() -> dict:
    assert _CI_PATH.is_file(), f"missing CI config at {_CI_PATH}"
    with _CI_PATH.open("r", encoding="utf-8") as fh:
        parsed = yaml.safe_load(fh)
    assert isinstance(parsed, dict), "CI config did not parse to a mapping"
    return parsed


def test_no_committed_plaintext_package_index():
    """F23: no repository-wide plaintext HTTP pip index / trusted host."""
    ci = _load_ci()
    variables = ci.get("variables") or {}

    index_url = str(variables.get("PIP_INDEX_URL", "") or "")
    assert not index_url.lower().startswith("http://"), (
        "a repository-wide plaintext http:// PIP_INDEX_URL lets an on-path "
        f"attacker inject packages into every job (got {index_url!r})"
    )

    # A trusted-host override only makes sense with an HTTP index and
    # disables TLS verification; it must not be a committed default.
    assert "PIP_TRUSTED_HOST" not in variables, (
        "PIP_TRUSTED_HOST must be an operator-supplied CI/CD variable, not a "
        "committed repository-wide default"
    )


def test_plaintext_index_is_fail_closed_without_explicit_opt_in():
    """F23: an http:// index must require an explicit opt-in gate."""
    ci = _load_ci()
    variables = ci.get("variables") or {}

    # The opt-in flag exists and defaults to *off*.
    assert str(variables.get("PLEXICHAT_CI_ALLOW_INSECURE_INDEX", "")) == "0", (
        "PLEXICHAT_CI_ALLOW_INSECURE_INDEX must default to 0 so a plaintext "
        "index is refused unless deliberately enabled"
    )

    # Every pip environment that installs dependencies inherits the guard
    # from .python_base.before_script; assert the gate itself is present and
    # actually rejects the insecure case.
    python_base = ci.get(".python_base") or {}
    before_script = "\n".join(
        str(step) for step in (python_base.get("before_script") or [])
    )
    assert "http://*" in before_script, (
        ".python_base.before_script must match a plaintext http:// index"
    )
    assert "PLEXICHAT_CI_ALLOW_INSECURE_INDEX" in before_script, (
        ".python_base.before_script must gate a plaintext index behind the "
        "explicit opt-in"
    )
    assert "exit 1" in before_script, (
        "the plaintext-index guard must fail the job (exit 1) rather than warn"
    )


def test_dind_sockets_require_tls():
    """F22: release/backup/cleanup jobs must talk to a TLS dind socket."""
    ci = _load_ci()
    dind_jobs = [
        name
        for name, job in ci.items()
        if isinstance(job, dict)
        and any(
            "dind" in str(svc.get("name", ""))
            if isinstance(svc, dict)
            else "dind" in str(svc)
            for svc in (job.get("services") or [])
        )
    ]
    assert dind_jobs, "expected at least one docker:dind job to guard"

    for name in dind_jobs:
        env = ci[name].get("variables") or {}
        assert str(env.get("DOCKER_TLS_CERTDIR", "")) != "", (
            f"{name}: DOCKER_TLS_CERTDIR must be non-empty so dind generates "
            "and trusts TLS certs"
        )
        host = str(env.get("DOCKER_HOST", ""))
        assert "2375" not in host, (
            f"{name}: DOCKER_HOST must use the TLS listener (2376), not the "
            f"unauthenticated 2375 socket (got {host!r})"
        )
        assert str(env.get("DOCKER_TLS_VERIFY", "")) == "1", (
            f"{name}: DOCKER_TLS_VERIFY must be enabled"
        )
