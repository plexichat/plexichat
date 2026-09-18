"""Guards for the bandit configuration.

Bandit used to be configured twice -- ``.bandit`` for the pre-commit hook and
``[tool.bandit]`` in ``pyproject.toml`` -- and the two drifted: the skip list,
the ``severity`` spelling and the ``targets`` all differed, so *which* rules ran
depended on how bandit happened to be invoked. The drift was not cosmetic: the
hook read ``.bandit``, which carried no ``exclude_dirs``, so a recursive run
descended into ``.venv`` and reported third-party findings as new relative to
``.bandit-baseline.json``.

``pyproject.toml`` is now the single source of truth and the hook reads it.
These tests keep it that way, and pin the review decisions that are easy to
undo by accident.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
BASELINE = REPO_ROOT / ".bandit-baseline.json"

BANDIT_CONFIG = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["bandit"]


def test_pyproject_is_the_only_bandit_config():
    """A second config file is exactly how the two silently diverged before."""
    rivals = [path for path in REPO_ROOT.glob(".bandit*") if path.name != BASELINE.name]
    rivals += [
        path
        for path in (REPO_ROOT / "bandit.yaml", REPO_ROOT / "bandit.yml")
        if path.exists()
    ]

    assert rivals == [], (
        "bandit settings belong in pyproject.toml ([tool.bandit]) only, so the "
        "hook and manual runs cannot drift apart; remove "
        f"{[path.name for path in rivals]}"
    )


def test_pre_commit_hook_reads_the_single_config():
    """The hook must point at pyproject.toml, not a file of its own."""
    hooks = [
        hook
        for repo in yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))[
            "repos"
        ]
        for hook in repo.get("hooks", [])
        if hook["id"] == "bandit"
    ]

    assert len(hooks) == 1, f"expected exactly one bandit hook, found {len(hooks)}"
    args = hooks[0].get("args", [])
    assert "-c" in args, f"bandit hook must pass an explicit config, got {args}"
    assert args[args.index("-c") + 1] == "pyproject.toml", (
        f"bandit hook must read the single config, got args={args}"
    )


def test_scan_targets_and_exclusions():
    """Rewriting these is how ``.venv`` crept back into the scan."""
    exclude_dirs = set(BANDIT_CONFIG["exclude_dirs"])
    # Without .venv in this list a recursive run reports third-party findings.
    assert ".venv" in exclude_dirs
    # Test-only findings (SHA1 helpers, mktemp) are reviewed and excluded.
    assert {"src/tests", "tests"} <= exclude_dirs
    # main.py is the documented entry point and is clean under this config.
    assert {"src", "main.py"} <= set(BANDIT_CONFIG["targets"])


def test_reviewed_skips_and_floors_are_stable():
    """The two decisions the drift had flipped in opposite directions."""
    skips = set(BANDIT_CONFIG.get("skips", []))
    # B601 (subprocess with shell=True) is reviewed and skipped deliberately.
    assert "B601" in skips
    # B101 (assert) is NOT skipped: an assert in production code is a real
    # finding. It is LOW severity, so the MEDIUM floor below already hides the
    # legitimate asserts in tests, and production uses `# nosec B101`.
    assert "B101" not in skips

    assert str(BANDIT_CONFIG["severity"]).upper().endswith("MEDIUM")
    assert str(BANDIT_CONFIG["confidence"]).upper().endswith("HIGH")
