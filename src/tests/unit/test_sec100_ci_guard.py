"""Tests for the SEC100 dangerous-pattern CI guard.

The guard (``ci-tooling/check_dangerous_patterns.py``) must do two things:
pass on the real ``src/`` tree so it never breaks CI, and fail on synthetic
trees that reintroduce each dangerous pattern. These tests cover both sides,
plus the reviewed-exception mechanisms (fingerprint, path waiver, inline
pragma) that keep the gate useful rather than noisy.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from types import ModuleType

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "ci-tooling" / "check_dangerous_patterns.py"
ALLOWLIST = REPO / "ci-tooling" / "dangerous_patterns_allowlist.json"

_GUARD: ModuleType | None = None


def _guard() -> ModuleType:
    """Load the standalone script (ci-tooling is not an importable package)."""
    global _GUARD
    if _GUARD is None:
        spec = importlib.util.spec_from_file_location("sec100_guard", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # ``Finding`` is a dataclass with postponed annotations; dataclasses
        # resolves those via sys.modules[cls.__module__], so register first.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _GUARD = module
    return _GUARD


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "src"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _kinds(findings: list[object]) -> list[str]:
    return [f.kind for f in findings]  # type: ignore[attr-defined]


def _write_allowlist(path: Path, entries: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The real tree must stay green
# ---------------------------------------------------------------------------


def test_gate_is_wired_into_ci() -> None:
    """The guard must stay a blocking job in the commit pipeline."""
    ci = (REPO / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert "supplychain:dangerous_patterns" in ci
    assert "ci-tooling/check_dangerous_patterns.py" in ci
    assert "if: '$RUN_LIVE_SMOKE != \"1\"'" in ci


@pytest.mark.timeout(300)
def test_real_tree_has_no_unallowlisted_findings() -> None:
    # Scanning the whole src/ tree walks ~2.5k files; the global 60s pytest
    # timeout is too tight on a loaded runner, so this one test opts into a
    # bound that still fails well before the CI job's 10m ceiling.
    guard = _guard()
    leftover = guard.scan(REPO / "src", ALLOWLIST)
    assert leftover == [], [f.to_dict() for f in leftover]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_eval_with_non_literal_is_flagged(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.py": "result = eval(user_input)\n"})
    assert "eval_exec" in _kinds(_guard().find_patterns(root))


def test_exec_with_non_literal_is_flagged(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.py": "exec(payload)\n"})
    assert "eval_exec" in _kinds(_guard().find_patterns(root))


def test_eval_with_literal_is_not_flagged(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.py": "result = eval('1 + 1')\n"})
    assert "eval_exec" not in _kinds(_guard().find_patterns(root))


def test_os_system_is_flagged_including_alias(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "a.py": "import os\nos.system('ls')\n",
            "b.py": "import os as _os\n_os.system('ls')\n",
        },
    )
    assert _kinds(_guard().find_patterns(root)).count("os_system") == 2


def test_shell_true_is_flagged_but_shell_false_is_not(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "a.py": "subprocess.run(cmd, shell=True)\n",
            "b.py": "subprocess.run(cmd, shell=False)\n",
        },
    )
    kinds = _kinds(_guard().find_patterns(root))
    assert "shell_true" in kinds
    assert kinds.count("shell_true") == 1


def test_indirect_eval_exec_spellings_are_flagged(tmp_path: Path) -> None:
    """Non-vacuous: indirect dispatch evades a direct-name-only detector."""
    snippets = [
        'builtins.__dict__["eval"](user_input)',
        'getattr(__builtins__, "exec")(payload)',
    ]
    guard = _guard()
    # The broadened pre-filter must let these reach the AST pass at all.
    assert all(guard.PREFILTER.search(s) for s in snippets)
    root = _tree(tmp_path, {f"b{i}.py": s + "\n" for i, s in enumerate(snippets)})
    assert _kinds(guard.find_patterns(root)).count("eval_exec") == 2


def test_exec_alias_assignment_is_flagged(tmp_path: Path) -> None:
    """Non-vacuous: binding the exec builtin to another name before calling it
    is an indirect dispatch that a name-only detector silently misses.
    """
    root = _tree(
        tmp_path,
        {
            "a.py": "run = exec\nrun(user_code)\n",
            "b.py": "def safe():\n    pass\nrun = safe\nrun(user_code)\n",  # clean
        },
    )
    assert _kinds(_guard().find_patterns(root)).count("eval_exec") == 1


def test_indirect_os_system_spellings_are_flagged(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "a.py": '__import__("os").system(cmd)\n',
            "b.py": 'import os\ngetattr(os, "system")(cmd)\n',
            "c.py": 'import importlib\nimportlib.import_module("os").system(cmd)\n',
        },
    )
    assert _kinds(_guard().find_patterns(root)).count("os_system") == 3


def test_shell_true_via_variable_and_kwargs_is_flagged(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "a.py": "subprocess.Popen(cmd, shell=USE_SHELL)\n",
            "b.py": 'subprocess.Popen(cmd, **{"shell": True})\n',
            "c.py": "subprocess.run(cmd, shell=False)\n",  # stays clean
        },
    )
    assert _kinds(_guard().find_patterns(root)).count("shell_true") == 2


def test_shell_true_via_dict_variable_is_flagged(tmp_path: Path) -> None:
    """Non-vacuous: splatting a dict variable built with a truthy shell key is
    the same shell sink as ``**{"shell": True}`` but evades a literal-only
    ``**kwargs`` check. A dict with shell=False must stay clean.
    """
    root = _tree(
        tmp_path,
        {
            "a.py": (
                "import subprocess\n"
                'opts = {"shell": True, "check": False}\n'
                "subprocess.run(cmd, **opts)\n"
            ),
            "b.py": (
                "import subprocess\n"
                'opts = {"shell": False}\n'
                "subprocess.run(cmd, **opts)\n"
            ),
        },
    )
    assert _kinds(_guard().find_patterns(root)).count("shell_true") == 1


def test_pickle_deserialization_is_flagged_including_alias(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "a.py": "import pickle as p\nobj = p.loads(payload)\n",
            "b.py": "from pickle import loads as l\nobj = l(payload)\n",
            "c.py": "import json\nobj = json.loads(payload)\n",  # not a sink
        },
    )
    guard = _guard()
    assert guard.PREFILTER.search("import pickle as p")
    assert _kinds(guard.find_patterns(root)).count("pickle_load") == 2


def test_pickle_unpickler_load_zero_arg_is_flagged(tmp_path: Path) -> None:
    """Non-vacuous: Unpickler(f).load() takes no args and was dead code under
    an ``if node.args`` gate. This is the common byte-buffer deserialization
    spelling and a direct RCE sink, so it must be reported.
    """
    root = _tree(
        tmp_path,
        {
            "a.py": "import pickle\npickle.Unpickler(f).load()\n",
            "b.py": "import pickle, io\npickle.Unpickler(io.BytesIO(payload)).load()\n",
            "c.py": "import json\nobj = json.load(f)\n",  # stays clean
        },
    )
    guard = _guard()
    assert all(
        guard.PREFILTER.search(s)
        for s in (
            "pickle.Unpickler(f).load()",
            "pickle.Unpickler(io.BytesIO(payload)).load()",
        )
    )
    assert _kinds(guard.find_patterns(root)).count("pickle_load") == 2


def test_hardcoded_token_is_flagged(tmp_path: Path) -> None:
    root = _tree(
        tmp_path, {"a.py": 'TOKEN = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"\n'}
    )
    findings = _guard().find_patterns(root)
    assert len(findings) == 1
    assert findings[0].kind == "hardcoded_secret"
    assert "GitHubToken" in findings[0].detail
    # The secret value itself must never leak into a finding message.
    assert "AAAAAAAAAAAAAAAAAAAA" not in findings[0].detail


def test_detector_regex_is_not_a_secret(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "a.py": (
                "import re\n"
                '_PEM_RE = re.compile(r"-----BEGIN RSA PRIVATE KEY-----[\\s\\S]*?")\n'
                '_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+")\n'
            )
        },
    )
    assert "hardcoded_secret" not in _kinds(_guard().find_patterns(root))


def test_bare_except_in_auth_code_is_flagged(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "auth/login.py": (
                "def do_login():\n"
                "    try:\n"
                "        verify()\n"
                "    except:\n"
                "        pass\n"
            )
        },
    )
    assert "bare_except_auth" in _kinds(_guard().find_patterns(root))


def test_bare_except_outside_auth_is_not_flagged(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "core/misc.py": (
                "def helper():\n    try:\n        work()\n    except:\n        pass\n"
            )
        },
    )
    assert "bare_except_auth" not in _kinds(_guard().find_patterns(root))


def test_re_raise_is_not_swallowing(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "auth/login.py": (
                "def do_login():\n"
                "    try:\n"
                "        verify()\n"
                "    except:\n"
                "        raise\n"
            )
        },
    )
    assert "bare_except_auth" not in _kinds(_guard().find_patterns(root))


def test_syntax_error_file_is_skipped_without_crashing(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"broken.py": "def f(:\n"})
    assert _guard().find_patterns(root) == []


def test_findings_are_deterministic(tmp_path: Path) -> None:
    root = _tree(
        tmp_path,
        {
            "b.py": "os.system('ls')\n",
            "a.py": "exec(payload)\n",
        },
    )
    guard = _guard()
    first = [f.to_dict() for f in guard.find_patterns(root)]
    second = [f.to_dict() for f in guard.find_patterns(root)]
    assert first == second
    assert [f["path"] for f in first] == ["a.py", "b.py"]


# ---------------------------------------------------------------------------
# Reviewed exceptions
# ---------------------------------------------------------------------------


def test_fingerprint_allowlist_suppresses_only_that_finding(tmp_path: Path) -> None:
    guard = _guard()
    root = _tree(tmp_path, {"a.py": "os.system('ls')\nexec(payload)\n"})
    findings = guard.find_patterns(root)
    system = next(f for f in findings if f.kind == "os_system")
    allowlist = _write_allowlist(
        tmp_path / "allow.json",
        [{"fingerprint": system.fingerprint, "reason": "reviewed legacy call"}],
    )
    leftover = guard.scan(root, allowlist)
    assert [f.kind for f in leftover] == ["eval_exec"]


def test_path_waiver_suppresses_only_its_category(tmp_path: Path) -> None:
    guard = _guard()
    root = _tree(
        tmp_path,
        {
            "tests/fixtures/tokens.py": 'TOKEN = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"\n',
        },
    )
    assert guard.scan(root, None) != []  # without a waiver it is a finding
    allowlist = _write_allowlist(
        tmp_path / "allow.json",
        [
            {
                "path_glob": "tests/**",
                "kind": "hardcoded_secret",
                "reason": "synthetic fixture token",
            }
        ],
    )
    assert guard.scan(root, allowlist) == []


def test_allowlist_entry_without_reason_is_rejected(tmp_path: Path) -> None:
    guard = _guard()
    allowlist = _write_allowlist(tmp_path / "allow.json", [{"fingerprint": "abc123"}])
    try:
        guard.load_allowlist(allowlist)
    except ValueError:
        pass
    else:  # pragma: no cover - failing this is the point of the test
        raise AssertionError("an allowlist entry without a reason must be rejected")


def test_inline_pragma_suppresses_finding(tmp_path: Path) -> None:
    guard = _guard()
    root = _tree(tmp_path, {"a.py": "os.system('ls')  # sec100: allow\n"})
    assert guard.scan(root, None) == []


def test_missing_allowlist_means_no_exceptions(tmp_path: Path) -> None:
    guard = _guard()
    root = _tree(tmp_path, {"a.py": "exec(payload)\n"})
    assert guard.scan(root, tmp_path / "does-not-exist.json") != []


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


def _cli(root: Path, allowlist: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--allowlist",
            str(allowlist),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_exits_zero_on_clean_tree(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.py": "x = 1\n"})
    result = _cli(root, tmp_path / "allow.json")
    assert result.returncode == 0, result.stderr
    assert "dangerous-pattern check OK" in result.stdout


def test_cli_exits_one_on_dangerous_tree(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.py": "result = eval(user_input)\n"})
    result = _cli(root, tmp_path / "allow.json")
    assert result.returncode == 1
    assert "eval_exec" in result.stderr
    assert "fingerprint:" in result.stderr


def test_cli_json_reports_every_finding(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"a.py": "os.system('ls')\n"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload[0]["kind"] == "os_system"
    assert "fingerprint" in payload[0]
