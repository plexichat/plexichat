"""Structural guard: every production YAML config parse goes through the bounded loader.

Follow-up to ``9dcf23f8`` (``fix(config): bound YAML parsing to prevent
config-file DoS``):

* ``safe_load_bounded`` / ``_BoundedSafeLoader`` in
  ``src/utils/common_utils/utils/config/core.py`` cap input size (2 MiB),
  nesting depth (64) and node count (50k), so a tampered operator config
  cannot turn a hot config read into a recursion / allocation DoS.
* The three config read sites -- ``ConfigLoader._load_config`` (core.py),
  ``_apply_explicit_allowlists`` (config/__init__.py) and
  ``_explicit_admin_allowed_hosts`` (api/routes/admin/utils.py) -- were all
  switched from raw ``yaml.safe_load`` to ``safe_load_bounded``.

Runtime tests cover the behaviour, but nothing stops a later edit from
re-introducing a raw ``yaml.safe_load`` in a config path, adding a brand new
YAML parse sink nobody thought to test, or deleting one of the caps.  This is
a pure AST ratchet (no app import / config / DB) over COMMITTED HEAD blobs:

1. ``_production_files`` enumerates every non-test production module under
   ``src/`` and ``_classify_yaml_calls`` labels each YAML parse / composer
   call ``bounded``, ``raw`` or ``composer``.  The ``raw`` set must be empty
   and the ``bounded`` set must be exactly the three known read sites.
2. ``_assert_caps_present`` binds the three literal caps and the bounded
   helper to the committed ``core.py`` source (removal / drift fails closed);
   ``_assert_alias_rejection_present`` binds the AliasEvent rejection inside
   ``_BoundedSafeLoader.compose_node`` (alias chains bypass the depth cap).
3. Synthetic self-tests prove the detectors are not vacuous: a synthetic
   ``yaml.safe_load(open("config.yaml"))`` is flagged; bounded calls and
   ``yaml.dump`` writes are accepted; cap removal and alias-guard removal are
   flagged.
4. ``test_scan_is_not_vacuous`` proves the committed-tree reader and sweep
   really operate on HEAD and find the three real bounded sites.

Blobs are read from COMMITTED HEAD (``git show HEAD:<path>``), not the working
tree: many production files are contested by parallel agents whose stale
copies can mislead a working-tree scan while a whole-file commit could
silently revert a fix.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

CORE_PATH = "src/utils/common_utils/utils/config/core.py"
ALLOWLISTS_PATH = "src/utils/common_utils/utils/config/__init__.py"
ADMIN_HOSTS_PATH = "src/api/routes/admin/utils.py"

# The only three production sites allowed to parse YAML, all bounded.
EXPECTED_READ_SITES = (CORE_PATH, ALLOWLISTS_PATH, ADMIN_HOSTS_PATH)

# Expected literal safety caps introduced by 9dcf23f8.
EXPECTED_CAPS = {
    "MAX_CONFIG_BYTES": 2 * 1024 * 1024,
    "MAX_CONFIG_DEPTH": 64,
    "MAX_CONFIG_NODES": 50_000,
}

# Production modules that legitimately import ``yaml``.  They must never
# contain a raw parse; ``yaml.dump*`` writes are safe and are the documented
# exception (generating / persisting default config files).
DOCUMENTED_YAML_MODULES = {
    "src/cli/main.py": (
        "yaml.dump writes only: generates default config/config.yaml; no parse"
    ),
    CORE_PATH: (
        "defines the bounded loader/helper (the single bounded read) and uses "
        "yaml.dump only to persist config"
    ),
}

# ---------------------------------------------------------------------------
# Vocabulary: what counts as a YAML parse sink
# ---------------------------------------------------------------------------

# Raw, unbounded parse entry points.  A new one in production must fail.
RAW_PARSE_ATTRS = frozenset(
    {
        "safe_load",
        "safe_load_all",
        "load",
        "load_all",
        "unsafe_load",
        "full_load",
        "compose",
        "compose_all",
    }
)

# Composer / low-level loader calls.  Legitimate only inside the bounded
# helper in core.py (they are the implementation of safe_load_bounded).
COMPOSER_ATTRS = frozenset({"get_single_data", "get_data", "compose_node"})

# Write-only sinks (documented safe exception, not a parse).
YAML_WRITE_ATTRS = frozenset({"dump", "safe_dump", "dump_all", "safe_dump_all"})

BOUNDED_NAME = "safe_load_bounded"


# ---------------------------------------------------------------------------
# Git / committed-tree plumbing
# ---------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run and pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


_EXCLUDED_COMPONENTS = frozenset({"tests", "selftest"})


def _is_excluded(path: str) -> bool:
    return any(part in _EXCLUDED_COMPONENTS for part in path.split("/")[:-1])


@lru_cache(maxsize=None)
def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr.strip()}"
    files = tuple(
        p for p in proc.stdout.splitlines() if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YamlCall:
    path: str
    lineno: int
    kind: str  # "raw" | "bounded" | "composer"
    call: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} [{self.kind}] {self.call}"


def _yaml_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """Return (module aliases, from-import map) for the yaml module.

    ``import yaml`` / ``import yaml as yy`` -> aliases ``{"yaml"|"yy"}``.
    ``from yaml import safe_load as sl`` -> ``{"sl": "safe_load"}``.
    """
    aliases: set[str] = set()
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "yaml":
                    aliases.add(alias.asname or "yaml")
        elif isinstance(node, ast.ImportFrom) and node.module == "yaml":
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name
    return aliases, imported


def _label(call: ast.Call) -> str:
    try:
        return ast.unparse(call)
    except Exception:  # pragma: no cover - defensive for exotic ASTs
        func = call.func
        return getattr(func, "attr", getattr(func, "id", "<call>"))


def _classify_yaml_calls(source: str, path: str) -> list[YamlCall]:
    """Classify every YAML parse / composer call in one module's source."""
    tree = ast.parse(source, filename=path)
    aliases, imported = _yaml_bindings(tree)
    calls: list[YamlCall] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            base = func.value
            base_name = base.id if isinstance(base, ast.Name) else None
            if attr in RAW_PARSE_ATTRS and base_name in aliases:
                calls.append(YamlCall(path, node.lineno, "raw", _label(node)))
            elif attr == BOUNDED_NAME:
                calls.append(YamlCall(path, node.lineno, "bounded", _label(node)))
            elif attr in COMPOSER_ATTRS:
                calls.append(YamlCall(path, node.lineno, "composer", _label(node)))
        elif isinstance(func, ast.Name):
            name = func.id
            if name == BOUNDED_NAME:
                calls.append(YamlCall(path, node.lineno, "bounded", _label(node)))
            elif imported.get(name) in RAW_PARSE_ATTRS:
                calls.append(YamlCall(path, node.lineno, "raw", _label(node)))

    return calls


def _imports_yaml(source: str) -> bool:
    aliases, imported = _yaml_bindings(ast.parse(source))
    return bool(aliases or imported)


def _int_value(node: ast.AST) -> int | None:
    """Evaluate a literal integer expression (e.g. ``2 * 1024 * 1024``)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        if not isinstance(node.value, bool):  # bool is an int subclass
            return node.value
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _int_value(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp):
        left, right = _int_value(node.left), _int_value(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.FloorDiv) and right:
            return left // right
    return None


def _module_constants(source: str) -> dict[str, int]:
    """Map name -> literal int for simple integer assignments in a module."""
    tree = ast.parse(source)
    consts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = _int_value(node.value)
            if value is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = value
    return consts


def _defines(tree: ast.AST, kind: type, name: str) -> bool:
    return any(
        isinstance(node, kind) and node.name == name for node in ast.walk(tree)
    )


def _assert_caps_present(source: str, *, label: str = CORE_PATH) -> None:
    """Assert the three literal caps and bounded helper exist in ``source``."""
    consts = _module_constants(source)
    for name, expected in EXPECTED_CAPS.items():
        assert name in consts, f"{label}: safety cap {name} was removed"
        assert consts[name] == expected, (
            f"{label}: safety cap {name} drifted from {expected} to {consts[name]}"
        )
    tree = ast.parse(source)
    assert _defines(tree, ast.ClassDef, "_BoundedSafeLoader"), (
        f"{label}: _BoundedSafeLoader was removed"
    )
    assert _defines(tree, ast.FunctionDef, BOUNDED_NAME), (
        f"{label}: {BOUNDED_NAME} helper was removed"
    )


def _alias_rejection_present(source: str) -> bool:
    """True if ``_BoundedSafeLoader.compose_node`` refuses YAML alias nodes.

    A chain of aliases keeps the parser's nesting depth at ~2 and the node
    count low, so all three caps pass while construction still blows the
    stack with a ``RecursionError`` that escapes the fail-closed handler.
    Config files have no need for anchors, so the loader must reject aliases
    outright.  Require both an ``AliasEvent`` reference and a ``raise`` in
    that method, so a dead reference with no rejection does not count.
    """
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ClassDef) and node.name == "_BoundedSafeLoader"):
            continue
        for item in node.body:
            if not (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == "compose_node"
            ):
                continue
            body = tuple(ast.walk(item))
            has_alias_event = any(
                isinstance(n, ast.Attribute) and n.attr == "AliasEvent" for n in body
            )
            has_raise = any(isinstance(n, ast.Raise) for n in body)
            if has_alias_event and has_raise:
                return True
    return False


def _assert_alias_rejection_present(source: str, *, label: str = CORE_PATH) -> None:
    """Assert the alias-based recursion bypass is still closed in ``source``."""
    assert _alias_rejection_present(source), (
        f"{label}: _BoundedSafeLoader.compose_node no longer rejects YAML "
        "AliasEvent nodes (alias chains bypass the depth cap)"
    )


@lru_cache(maxsize=None)
def _sweep(commit: str) -> tuple[YamlCall, ...]:
    """Classify YAML calls across every production module in HEAD."""
    reader = _make_blob_reader(commit)
    found: list[YamlCall] = []
    for path in _production_files(commit):
        found.extend(_classify_yaml_calls(reader(path), path))
    return tuple(found)


# ---------------------------------------------------------------------------
# Synthetic self-tests (the detectors are not vacuous)
# ---------------------------------------------------------------------------


def test_self_test_raw_safe_load_in_config_is_flagged():
    """A freshly added raw ``yaml.safe_load`` in config code must be caught."""
    source = (
        "import yaml\n"
        "\n"
        "def load_config():\n"
        "    with open('config.yaml') as fh:\n"
        "        return yaml.safe_load(fh)\n"
    )
    findings = _classify_yaml_calls(source, "src/api/_synthetic.py")
    raw = [f for f in findings if f.kind == "raw"]
    assert raw, "detector failed to flag a raw yaml.safe_load(open(config)) call"
    assert any("safe_load" in f.call for f in raw)


def test_self_test_raw_load_variants_are_flagged():
    """yaml.load / unsafe_load / compose and from-import aliases are all raw."""
    source = (
        "import yaml as yy\n"
        "from yaml import full_load\n"
        "\n"
        "def f():\n"
        "    yy.load('x')\n"
        "    yy.unsafe_load('x')\n"
        "    yy.compose('x')\n"
        "    full_load('x')\n"
    )
    kinds = {f.call.split("(")[0]: f.kind for f in _classify_yaml_calls(source, "s.py")}
    assert all(k == "raw" for k in kinds.values()), kinds
    assert len(kinds) == 4, kinds


def test_self_test_bounded_call_and_dump_write_are_accepted():
    """Bounded parses and dump writes are the documented safe cases."""
    source = (
        "import yaml\n"
        "from ...config.core import safe_load_bounded\n"
        "\n"
        "def write_and_read(path, fh):\n"
        "    yaml.dump({'a': 1}, fh)\n"
        "    return safe_load_bounded(fh)\n"
    )
    findings = _classify_yaml_calls(source, "src/utils/_synthetic.py")
    assert not [f for f in findings if f.kind == "raw"], findings
    assert len([f for f in findings if f.kind == "bounded"]) == 1, findings


# Minimal, guaranteed-parseable stand-in for core.py used only by the
# cap-detector self-test (the real committed file is checked separately).
_SYNTHETIC_CORE = (
    "import yaml\n"
    "MAX_CONFIG_BYTES = 2 * 1024 * 1024\n"
    "MAX_CONFIG_DEPTH = 64\n"
    "MAX_CONFIG_NODES = 50_000\n"
    "\n"
    "class ConfigParseError(yaml.YAMLError):\n"
    "    pass\n"
    "\n"
    "class _BoundedSafeLoader(yaml.SafeLoader):\n"
    "    def compose_node(self, parent, index):\n"
    "        if self.check_event(yaml.events.AliasEvent):\n"
    "            raise ConfigParseError('aliases not allowed')\n"
    "        return super().compose_node(parent, index)\n"
    "\n"
    "def safe_load_bounded(source):\n"
    "    return {}\n"
)


def test_self_test_cap_removal_and_drift_are_flagged():
    """Deleting or changing a cap (or the helper) must fail the cap check."""
    _assert_caps_present(_SYNTHETIC_CORE, label="synthetic")  # baseline passes

    no_caps = "\n".join(
        line for line in _SYNTHETIC_CORE.splitlines() if "MAX_CONFIG_" not in line
    )
    with pytest.raises(AssertionError, match="removed"):
        _assert_caps_present(no_caps, label="synthetic")

    drifted = _SYNTHETIC_CORE.replace("MAX_CONFIG_DEPTH = 64", "MAX_CONFIG_DEPTH = 4096")
    with pytest.raises(AssertionError, match="drifted"):
        _assert_caps_present(drifted, label="synthetic")

    no_helper = _SYNTHETIC_CORE.replace(
        "def safe_load_bounded(source):", "def _renamed_helper(source):"
    )
    with pytest.raises(AssertionError, match="removed"):
        _assert_caps_present(no_helper, label="synthetic")

    no_loader = _SYNTHETIC_CORE.replace(
        "class _BoundedSafeLoader(yaml.SafeLoader):", "class _RenamedLoader(yaml.SafeLoader):"
    )
    with pytest.raises(AssertionError, match="removed"):
        _assert_caps_present(no_loader, label="synthetic")


def test_self_test_alias_guard_removal_is_flagged():
    """Deleting the alias rejection (or just its raise) must fail closed."""
    _assert_alias_rejection_present(_SYNTHETIC_CORE, label="synthetic")  # baseline passes

    # Remove the whole rejection block: still valid Python, but no guard.
    alias_block = (
        "        if self.check_event(yaml.events.AliasEvent):\n"
        "            raise ConfigParseError('aliases not allowed')\n"
    )
    no_alias = _SYNTHETIC_CORE.replace(alias_block, "")
    with pytest.raises(AssertionError, match="AliasEvent"):
        _assert_alias_rejection_present(no_alias, label="synthetic")

    # Keep the AliasEvent reference but drop the raise: a dead check is not a
    # rejection. Replace the raise with `pass` so the snippet stays parseable.
    no_raise = _SYNTHETIC_CORE.replace(
        "            raise ConfigParseError('aliases not allowed')\n",
        "            pass\n",
    )
    with pytest.raises(AssertionError, match="AliasEvent"):
        _assert_alias_rejection_present(no_raise, label="synthetic")


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in EXPECTED_READ_SITES:
        assert required in files, f"{required} unexpectedly excluded from scan"

    # The reader returns real committed content: caps + helper + alias guard
    # must be in HEAD.
    source = _make_blob_reader(commit_sha)(CORE_PATH)
    _assert_caps_present(source)
    _assert_alias_rejection_present(source)
    assert "class _BoundedSafeLoader(yaml.SafeLoader)" in source
    assert "loader.get_single_data()" in source

    # The detector actually finds the three bounded parses in HEAD, so the
    # "no raw parse" / "all parses bounded" assertions below are not vacuous.
    bounded = {f.path for f in _sweep(commit_sha) if f.kind == "bounded"}
    assert bounded == set(EXPECTED_READ_SITES), bounded


def test_three_caps_still_present_in_committed_core(commit_sha):
    _assert_caps_present(_make_blob_reader(commit_sha)(CORE_PATH))


def test_alias_rejection_still_present_in_committed_loader(commit_sha):
    _assert_alias_rejection_present(_make_blob_reader(commit_sha)(CORE_PATH))


def test_all_three_read_sites_call_safe_load_bounded(commit_sha):
    reader = _make_blob_reader(commit_sha)
    for path in EXPECTED_READ_SITES:
        findings = _classify_yaml_calls(reader(path), path)
        bounded = [f for f in findings if f.kind == "bounded"]
        raw = [f for f in findings if f.kind == "raw"]
        assert len(bounded) == 1, f"{path}: expected 1 bounded parse, got {bounded}"
        assert not raw, f"{path}: raw parse reintroduced: {raw}"


def test_no_raw_yaml_parse_in_production(commit_sha):
    offenders = [f for f in _sweep(commit_sha) if f.kind == "raw"]
    assert not offenders, (
        "New raw / unbounded YAML parse in production code -- use "
        "safe_load_bounded (config.core) instead:\n  "
        + "\n  ".join(str(f) for f in sorted(offenders, key=lambda f: (f.path, f.lineno)))
    )


def test_every_production_yaml_parse_is_one_of_the_three_bounded_sites(commit_sha):
    bounded = sorted({f.path for f in _sweep(commit_sha) if f.kind == "bounded"})
    assert bounded == sorted(EXPECTED_READ_SITES), (
        "set of production YAML parse sites drifted; expected exactly the "
        f"three bounded read sites.\n  got: {bounded}\n"
        f"  expected: {sorted(EXPECTED_READ_SITES)}"
    )


def test_composer_calls_only_live_inside_the_bounded_helper(commit_sha):
    offenders = [f for f in _sweep(commit_sha) if f.kind == "composer" and f.path != CORE_PATH]
    assert not offenders, (
        "Low-level YAML composer call outside the bounded helper:\n  "
        + "\n  ".join(str(f) for f in offenders)
    )


def test_yaml_imports_are_exactly_the_documented_modules(commit_sha):
    reader = _make_blob_reader(commit_sha)
    importers = set()
    for path in _production_files(commit_sha):
        if _imports_yaml(reader(path)):
            importers.add(path)
    assert importers == set(DOCUMENTED_YAML_MODULES), (
        "production modules importing yaml drifted from the documented "
        f"(write-only / bounded-helper) set.\n  got: {sorted(importers)}\n"
        f"  expected: {sorted(DOCUMENTED_YAML_MODULES)}"
    )
    for path, reason in DOCUMENTED_YAML_MODULES.items():
        assert reason.strip(), f"{path}: empty documentation reason"
