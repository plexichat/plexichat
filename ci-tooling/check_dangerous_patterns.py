#!/usr/bin/env python3
"""Deterministic CI guard against dangerous Python patterns in ``src/``.

This is a small, dependency-free static check. It parses every ``*.py`` file
under ``src/`` with :mod:`ast` (plus a few regexes for credential *shapes*) and
fails when it finds any of:

* ``eval()`` / ``exec()`` called with a non-literal argument — dynamic code
  execution on data the interpreter cannot see at authoring time. Indirect
  spellings (``builtins.__dict__["eval"]``, ``getattr(__builtins__, "exec")``)
  are resolved too.
* ``os.system()`` — shell command construction with no argument separation,
  including ``__import__("os").system()`` and ``getattr(os, "system")()``.
* a call with a truthy ``shell=`` (literal, variable or ``**kwargs``) — the
  subprocess equivalent of ``os.system()``.
* ``pickle.load()`` / ``pickle.loads()`` — deserialization of untrusted data,
  including aliased imports.
* a hardcoded credential token (AWS/GitHub/GitLab/Slack/Stripe/OpenAI key,
  JWT, PEM private key) embedded as a string literal. Detector *regexes* such
  as ``re.compile(r"...PRIVATE KEY...")`` are deliberately not findings.
* a bare ``except:`` that swallows errors in an authentication path (the file
  path or an enclosing function/class name mentions auth, login, token, ...).

It has no third-party imports so it runs in a bare ``python:3.14-slim``
container before dependencies are installed.

Findings are fingerprinted by *path + kind + source text* (never line number),
so refactors that move code around do not invalidate a reviewed exception.
Known/accepted findings are listed in ``ci-tooling/dangerous_patterns_allowlist.json``;
an individual line can also carry ``# sec100: allow`` (or ``# noqa: SEC100``).
A new finding that is neither allowlisted nor annotated fails the check.

Usage::

    python ci-tooling/check_dangerous_patterns.py                 # scan repo/src
    python ci-tooling/check_dangerous_patterns.py --root PATH
    python ci-tooling/check_dangerous_patterns.py --json          # all findings
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# ---------------------------------------------------------------------------
# Detection configuration
# ---------------------------------------------------------------------------

KIND_DESCRIPTIONS = {
    "eval_exec": "eval()/exec() with a non-literal argument",
    "os_system": "os.system() call",
    "shell_true": "call with shell=True (literal, variable, or **kwargs)",
    "pickle_load": "pickle.load()/loads() on a possibly untrusted payload",
    "hardcoded_secret": "hardcoded credential token",
    "bare_except_auth": "bare except swallowing errors in auth code",
}

EVAL_NAMES = frozenset({"eval", "exec"})

# High-confidence credential shapes. These are intentionally narrow: a broad
# "name looks like a password" heuristic produces false positives on event
# names, cookie names and env-var *names* (see the audit that motivated this
# gate). General secret scanning is left to detect-secrets / bandit baselines.
TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AWSKey",
        re.compile(r"(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|AGPA)[A-Z0-9]{16}\b"),
    ),
    (
        "GitHubToken",
        re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    ),
    ("GitLabToken", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    ("SlackToken", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("StripeKey", re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    ("OpenAIKey", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    (
        "JWT",
        re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ),
    (
        "PrivateKey",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        ),
    ),
)

# Cheap pre-filter. Only files whose text contains one of these candidate
# markers are handed to ast.parse; parsing all ~2.5k files takes over a minute
# and would push the gate past the per-test timeout in CI. The pre-filter is
# deliberately *broader* than the real rules (it may over-match, never miss).
PREFILTER = re.compile(
    # Deliberately broad. In particular ``shell=`` (not just ``shell=True``),
    # ``getattr``/``__import__``/``import_module``/``__dict__`` (indirect sink
    # resolution) and ``pickle`` must all reach the AST pass — otherwise a sink
    # spelled indirectly would be skipped before it is ever examined. May
    # over-match; must never miss.
    r"\beval\b|\bexec\b|\bsystem\b|\bshell\b|\bgetattr\s*\(|\b__import__\s*\(|"
    r"\bimport_module\s*\(|__dict__|\bpickle\b|\bexcept\s*:|"
    r"AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|AGPA|gh[pousr]_|github_pat_|glpat-|"
    r"xox[baprs]-|(?:sk|rk)_(?:live|test)_|sk-[A-Za-z0-9_-]{32}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.|"
    r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
)

# A string fed to one of these is a detector pattern, not a credential.
REGEX_FUNCS = frozenset(
    {
        "compile",
        "match",
        "search",
        "sub",
        "subn",
        "fullmatch",
        "findall",
        "finditer",
        "split",
        "escape",
    }
)

AUTH_HINTS = (
    "auth",
    "login",
    "logout",
    "oauth",
    "token",
    "session",
    "credential",
    "password",
    "passkey",
    "permission",
    "cookie",
    "signin",
    "signup",
    "verify",
)

ALLOW_MARKERS = ("sec100: allow", "noqa: sec100")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A single dangerous-pattern finding, with a stable fingerprint."""

    path: str  # posix, relative to the scan root
    line: int  # 1-based, for humans only — not part of the fingerprint
    kind: str  # one of KIND_DESCRIPTIONS
    detail: str
    fingerprint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "detail": self.detail,
            "fingerprint": self.fingerprint,
        }


def _fingerprint(path: str, kind: str, detail: str) -> str:
    """Stable identity for a finding; independent of line numbers."""
    payload = f"{path}\0{kind}\0{detail}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _segment(node: ast.AST, source: str, fallback: str) -> str:
    """Normalised source text for a node (collapsed whitespace)."""
    try:
        seg = ast.get_source_segment(source, node)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover - defensive for exotic nodes
        seg = None
    return " ".join((seg or fallback).split())


def _is_auth_context(path: str, parents: dict[ast.AST, ast.AST], node: ast.AST) -> bool:
    """True when a node sits in auth-looking code."""
    haystack = [path.lower()]
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            haystack.append(cur.name.lower())
        cur = parents.get(cur)
    return any(hint in part for hint in AUTH_HINTS for part in haystack)


def _is_regex_call(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr in REGEX_FUNCS
    if isinstance(func, ast.Name):
        return func.id in REGEX_FUNCS
    return False


def _getattr_attr_name(node: ast.Call) -> str | None:
    """The constant attribute name in ``getattr(obj, "name")``, if any."""
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _is_getattr_call(node: ast.expr, attr: str | None = None) -> bool:
    """True for ``getattr(...)``; *attr*, when given, must be the constant name."""
    if not isinstance(node, ast.Call):
        return False
    name = _getattr_attr_name(node)
    return name is not None and (attr is None or name == attr)


def _dynamic_call_name(func: ast.expr) -> str | None:
    """Best-effort static name for a callable expression.

    Resolves the indirect spellings a real RCE sink hides behind:
    ``builtins.eval`` / ``x.exec`` (Attribute), ``builtins.__dict__["eval"]``
    (Subscript) and ``getattr(__builtins__, "exec")`` (Call). Returns None when
    the target cannot be named statically (a plain helper function, etc.).
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Subscript):
        sl = func.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
        return None
    if isinstance(func, ast.Call):
        return _getattr_attr_name(func)
    return None


def _is_os_module_expr(node: ast.expr, os_aliases: set[str]) -> bool:
    """True when *node* names the ``os`` module: direct, aliased, or dynamic."""
    if isinstance(node, ast.Name):
        return node.id in os_aliases
    if isinstance(node, ast.Call) and node.args:
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "os"):
            return False
        fn = node.func
        return (isinstance(fn, ast.Name) and fn.id == "__import__") or (
            isinstance(fn, ast.Attribute) and fn.attr == "import_module"
        )
    return False


def _shell_is_truthy(value: ast.expr) -> bool:
    """A ``shell=`` value that is not a literal false is a shell risk.

    Mirrors the SEC400 invariant: ``shell=True``, ``shell=1`` and a
    non-constant expression (``shell=FLAG``) all enable the shell.
    """
    if isinstance(value, ast.Constant):
        return bool(value.value)
    return True


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------


def _analyse(path: Path, rel: str, source: str) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        # A file that does not parse cannot hide a *new* executable pattern
        # from a reviewer diff, and failing the whole gate on it would be
        # nondeterministic with respect to unrelated syntax work. Skip it.
        return []

    # One walk gathers the parent map plus the two facts the rules need:
    # aliases for `os` (so `import os as _os; _os.system(...)` is caught) and
    # constants that are arguments to a regex call (detector patterns such as
    # the PEM regex in core/telemetry/scrubbing.py, not credentials). Keeping
    # this to a single pass matters: parsing dominates the scan's cost.
    parents: dict[ast.AST, ast.AST] = {}
    os_aliases = {"os"}
    pickle_aliases = {"pickle"}
    pickle_loaded_names: set[str] = set()
    regex_arg_ids: set[int] = set()
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
        if isinstance(parent, ast.Import):
            for alias in parent.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or "os")
                if alias.name == "pickle":
                    pickle_aliases.add(alias.asname or "pickle")
        elif isinstance(parent, ast.ImportFrom) and parent.module == "os":
            for alias in parent.names:
                if alias.name == "system":
                    os_aliases.add(alias.asname or "system")
        elif isinstance(parent, ast.ImportFrom) and parent.module == "pickle":
            for alias in parent.names:
                if alias.name in ("load", "loads"):
                    pickle_loaded_names.add(alias.asname or alias.name)
        elif isinstance(parent, ast.Call) and _is_regex_call(parent.func):
            for arg in parent.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Constant):
                        regex_arg_ids.add(id(sub))

    findings: list[Finding] = []

    def add(node: ast.AST, kind: str, detail: str, fallback: str) -> None:
        norm = _segment(node, source, fallback)
        findings.append(
            Finding(rel, node.lineno, kind, detail, _fingerprint(rel, kind, norm))
        )

    for node in parents:
        # --- eval / exec with a non-literal argument -----------------------
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = _dynamic_call_name(func)
            if name in EVAL_NAMES and not isinstance(node.args[0], ast.Constant):
                add(node, "eval_exec", "dynamic eval/exec argument", "eval()")

            # --- os.system --------------------------------------------------
            # Covers direct/aliased imports plus dynamic resolution:
            # ``__import__("os").system()``, ``importlib.import_module("os").system()``
            # and ``getattr(os, "system")()``.
            if isinstance(func, ast.Attribute) and func.attr == "system":
                if _is_os_module_expr(func.value, os_aliases):
                    add(node, "os_system", "os.system()", "os.system()")
            elif (
                isinstance(func, ast.Name) and func.id in os_aliases and func.id != "os"
            ):
                # `from os import system` (possibly aliased).
                add(node, "os_system", "os.system() import", "system()")
            elif _is_getattr_call(func, "system") and _is_os_module_expr(
                func.args[0], os_aliases
            ):
                add(
                    node,
                    "os_system",
                    "os.system() via getattr",
                    "getattr(os,'system')()",
                )

            # --- shell=True (literal, variable, or **kwargs spelling) -------
            for kw in node.keywords:
                if kw.arg == "shell" and _shell_is_truthy(kw.value):
                    add(node, "shell_true", "shell=True", "shell=True")
                if kw.arg is None and isinstance(kw.value, ast.Dict):
                    for key, value in zip(kw.value.keys, kw.value.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "shell"
                            and _shell_is_truthy(value)
                        ):
                            add(
                                node,
                                "shell_true",
                                "shell=True via **kwargs",
                                "shell=True",
                            )

            # --- pickle deserialization (aliased import / from-import) ------
            if isinstance(func, ast.Name) and func.id in pickle_loaded_names:
                add(node, "pickle_load", "pickle.load(s)()", "pickle.loads()")
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in ("load", "loads")
                and isinstance(func.value, ast.Name)
                and func.value.id in pickle_aliases
            ):
                add(node, "pickle_load", "pickle.load(s)()", "pickle.loads()")
            elif (
                isinstance(func, ast.Attribute)
                and func.attr in ("load", "loads")
                and isinstance(func.value, ast.Call)
                and _dynamic_call_name(func.value.func) == "Unpickler"
            ):
                add(
                    node,
                    "pickle_load",
                    "pickle.Unpickler().load()",
                    "Unpickler().load()",
                )

        # --- hardcoded credential tokens ----------------------------------
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in regex_arg_ids:
                value = node.value
                # Obvious detector scaffolding is never a credential.
                if not any(mark in value for mark in ("\\s", "(?", "[A-Z", "{16}")):
                    for label, pattern in TOKEN_PATTERNS:
                        match = pattern.search(value)
                        if match:
                            token_hash = hashlib.sha256(
                                match.group(0).encode("utf-8")
                            ).hexdigest()[:12]
                            add(
                                node,
                                "hardcoded_secret",
                                f"{label} token sha256:{token_hash}",
                                "token literal",
                            )
                            break

        # --- bare except swallowing errors in auth code -------------------
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            body = node.body
            swallows = any(isinstance(stmt, ast.Pass) for stmt in body) and not any(
                isinstance(stmt, ast.Raise) for stmt in body
            )
            if swallows and _is_auth_context(rel, parents, node):
                add(
                    node,
                    "bare_except_auth",
                    "bare except swallows auth error",
                    "except:",
                )

    # A single source line can only be a finding once per (line, kind).
    unique = {(f.line, f.kind, f.fingerprint): f for f in findings}
    return [unique[key] for key in sorted(unique)]


def find_patterns(root: Path) -> list[Finding]:
    """Return every finding under *root*, deterministically ordered."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"scan root is not a directory: {root}")
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not PREFILTER.search(source):
            continue
        rel = path.relative_to(root).as_posix()
        findings.extend(_analyse(path, rel, source))
    return sorted(findings, key=lambda f: (f.path, f.line, f.kind, f.detail))


# ---------------------------------------------------------------------------
# Allowlist / suppression
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Allowlist:
    """Reviewed exceptions: exact fingerprints and path-scoped waivers."""

    fingerprints: frozenset[str]
    # (path glob relative to the scan root, finding kind)
    waivers: tuple[tuple[str, str], ...]


def load_allowlist(path: Path | None) -> Allowlist:
    """Load reviewed exceptions from the allowlist JSON (missing -> empty).

    Two entry forms are accepted:

    * ``{"fingerprint": "...", "reason": "..."}`` — one exact finding.
    * ``{"path_glob": "tests/**", "kind": "hardcoded_secret", "reason": "..."}``
      — a documented waiver for a whole category in a path scope. Used for
      synthetic credentials in test fixtures, which detect-secrets' own
      pre-commit hook excludes for the same reason.

    A waiver without a non-empty ``reason`` is rejected: exceptions must be
    reviewable, not silent.
    """
    if path is None or not path.is_file():
        return Allowlist(frozenset(), ())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"cannot read allowlist {path}: {exc}") from exc

    fingerprints: set[str] = set()
    waivers: list[tuple[str, str]] = []
    for entry in data.get("entries", []):
        if not isinstance(entry, dict) or not str(entry.get("reason", "")).strip():
            raise ValueError(f"allowlist entry in {path} has no reason")
        fingerprint = entry.get("fingerprint")
        path_glob = entry.get("path_glob")
        kind = entry.get("kind")
        if isinstance(fingerprint, str) and fingerprint:
            fingerprints.add(fingerprint)
        elif (
            isinstance(path_glob, str) and path_glob and isinstance(kind, str) and kind
        ):
            waivers.append((path_glob, kind))
        else:
            raise ValueError(
                f"allowlist entry in {path} is not a fingerprint or waiver"
            )
    return Allowlist(frozenset(fingerprints), tuple(waivers))


def filter_findings(
    findings: list[Finding], root: Path, allowed: Allowlist
) -> list[Finding]:
    """Drop allowlisted and inline-annotated findings."""
    kept: list[Finding] = []
    for finding in findings:
        if finding.fingerprint in allowed.fingerprints:
            continue
        if any(
            fnmatch.fnmatchcase(finding.path, glob) and finding.kind == kind
            for glob, kind in allowed.waivers
        ):
            continue
        # Inline suppression is judged against the finding's own source line.
        path = root / finding.path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            lines = []
        line_text = (
            lines[finding.line - 1].lower() if 0 < finding.line <= len(lines) else ""
        )
        if any(marker in line_text for marker in ALLOW_MARKERS):
            continue
        kept.append(finding)
    return kept


def scan(root: Path, allowlist: Path | None) -> list[Finding]:
    """Return unallowlisted findings for *root*."""
    return filter_findings(find_patterns(root), root, load_allowlist(allowlist))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO / "src",
        help="tree to scan (default: <repo>/src)",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=HERE / "dangerous_patterns_allowlist.json",
        help="reviewed-exceptions JSON (default: ci-tooling/dangerous_patterns_allowlist.json)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit every finding (with fingerprints) as JSON; do not apply the allowlist",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"FAIL: scan root {root} does not exist", file=sys.stderr)
        return 2

    all_findings = find_patterns(root)
    if args.json:
        print(json.dumps([f.to_dict() for f in all_findings], indent=2, sort_keys=True))
        return 0

    try:
        leftover = filter_findings(all_findings, root, load_allowlist(args.allowlist))
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if leftover:
        print("FAIL: dangerous Python patterns detected in src/", file=sys.stderr)
        for finding in leftover:
            desc = KIND_DESCRIPTIONS.get(finding.kind, finding.kind)
            print(
                f"  - {finding.path}:{finding.line}: [{finding.kind}] "
                f"{desc} ({finding.detail})",
                file=sys.stderr,
            )
            print(f"      fingerprint: {finding.fingerprint}", file=sys.stderr)
        print(
            "Review each finding; fix it, annotate the line with "
            "'# sec100: allow', or add its fingerprint to the allowlist "
            "with a written rationale.",
            file=sys.stderr,
        )
        return 1

    print(
        f"dangerous-pattern check OK: {len(all_findings)} finding(s) reviewed, 0 unallowlisted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
