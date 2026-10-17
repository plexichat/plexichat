"""Guard the ruff hook's exclusion list against masking source packages.

The ruff pre-commit hook passes ``--extend-exclude`` with a mix of runtime
directories (``.venv``, ``temp_test_*``) and source-tree paths that have to be
root-anchored. Ruff matches a *bare* pattern such as ``cache`` against any
directory of that name, so a bare token silently skips the real package
``src/core/database/cache`` -- exactly how ``media`` hid ``src/core/media`` and
``config`` hid ``src/utils/common_utils/{utils,tests}/config`` until they were
anchored to ``/media`` and ``/config``.

A reverted anchor (or a new bare token) does not fail anything on its own: it
just stops linting a tracked package, so this test fails when a bare token
matches a directory that still holds an ``__init__.py`` under ``src/``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
SRC_ROOT = REPO_ROOT / "src"


def _ruff_extend_exclude_tokens() -> list[str]:
    """Return the comma-separated tokens the ruff hook excludes."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook["id"] != "ruff":
                continue
            args = hook.get("args", [])
            assert "--extend-exclude" in args, (
                f"ruff hook no longer passes an explicit exclusion list: {args}"
            )
            raw = args[args.index("--extend-exclude") + 1]
            return [token.strip() for token in raw.split(",") if token.strip()]
    raise AssertionError("no ruff hook found in .pre-commit-config.yaml")


def _source_package_names() -> set[str]:
    """Names of directories under ``src/`` that are importable packages."""
    return {path.parent.name for path in SRC_ROOT.rglob("__init__.py")}


def test_ruff_excludes_do_not_shadow_source_packages():
    packages = _source_package_names()
    masked = [
        token
        for token in _ruff_extend_exclude_tokens()
        # A leading ``/`` anchors the token to the repo root and globs are
        # deliberately broad; neither can shadow a nested package.
        if not token.startswith("/")
        and not any(char in token for char in "*?[")
        and token in packages
    ]

    assert masked == [], (
        "bare ruff --extend-exclude tokens shadow tracked source packages: "
        f"{masked}. Anchor them to the repo root (e.g. '/cache') so the "
        "package is linted again."
    )


def test_ruff_excludes_are_root_anchored_for_known_packages():
    """The three packages that already had to be rescued stay anchored."""
    tokens = set(_ruff_extend_exclude_tokens())
    for name in ("media", "config", "cache"):
        assert f"/{name}" in tokens, (
            f"expected '/{name}' in the ruff exclusion list so src/**/{name} "
            f"is linted; got {sorted(tokens)}"
        )
        assert name not in tokens, (
            f"bare '{name}' in the ruff exclusion list would mask every "
            f"directory named {name}, including the tracked package"
        )
