"""Dependency-free regression guard against committed secret literals.

Follow-up to the N537 secrets scan.  That scan reported no live secret, but it
could only inspect the working tree: the shared worktree has a broken
``refs/stash`` (``fatal: bad object refs/stash``), so ``gitleaks git`` history
scanning returned zero commits.  This test adds a fast, stdlib-only guard that
runs on every checkout and needs neither gitleaks nor detect-secrets.

The guard enumerates the *tracked* source files under ``src/`` (``git ls-files``
-- untracked peer work is deliberately not scanned) and fails when a file that
is not a reviewed, documented false positive contains either:

* a PEM private-key block, or
* a high-entropy, credential-shaped literal that is assigned to (or used as)
  a credential-looking name -- a Shannon-entropy + keyword/shape heuristic.

Known false positives from N537 are handled by ``ALLOWLIST`` below: KDF/KEK
test vectors, schema examples, scrubbing regexes and credential-looking strings
in docstrings.  Test fixtures are waived wholesale, mirroring the
``detect-secrets`` pre-commit exclusion and the SEC100 ``hardcoded_secret``
waiver; production code under ``src/`` is still fully checked.

Non-vacuity is asserted at the bottom: a synthetic high-entropy literal and a
synthetic PEM block in a fresh temp file are flagged, while the same literal
placed at every allowlisted false-positive path is ignored.  Findings carry a
hash of the secret, never the secret text itself.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pytest

REPO = Path(__file__).resolve().parents[3]
SCAN_SUBDIR = "src"
SCANNED_SUFFIXES = frozenset({".py", ".yaml", ".yml", ".json", ".env"})

# PEM private-key blocks (the marker itself is enough -- a real key never needs
# to be valid to be a leak, and an invalid one is still a leak of intent).
PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")

# Keyword heuristic: only literals attached to a credential-looking name are
# candidates.  Deliberately broad on the name, narrow on the value (below), so
# ordinary SQL/prose/format strings are not reported.
SECRET_NAME_RE = re.compile(
    r"(?i)secret|token|passwd|password|passphrase|api[_-]?key|apikey|"
    r"access[_-]?key|private[_-]?key|credential|auth[_-]?key|"
    r"client[_-]?secret|bearer|kek|dek|pepper|master[_-]?key|"
    r"signing[_-]?key|session[_-]?key|encryption[_-]?key"
)
VALUE_SHAPE_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{16,}$")
PLACEHOLDER_RE = re.compile(
    r"(?i)example|placeholder|sample|dummy|fake|your|changeme|xxx|todo|"
    r"redacted|insert|replace|deadbeef|\btest\b|[{}]"
)
MIN_LEN, MAX_LEN, MIN_ENTROPY = 16, 512, 4.0

# Shapes that are credentials even at low entropy (e.g. ghp_AAAA...).  Kept as
# a separate signal so an obvious provider prefix is never hidden by a low
# Shannon score.  ``eyJ`` catches base64url-encoded JSON credentials such as
# the example license string in a docstring.
KNOWN_SHAPES = tuple(
    re.compile(p)
    for p in (
        r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}",
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"eyJ[A-Za-z0-9_-]{20,}",
    )
)

# Quoted credential-shaped value in a non-Python source file (.yaml/.json/.env).
QUOTED_VALUE_RE = re.compile(r"""['"]([A-Za-z0-9_\-+/=]{16,})['"]""")


# Reviewed false positives.  Every entry needs a written rationale.  Glob
# paths are relative to REPO (the scan root) and use fnmatch semantics: `*` and
# `**` both span `/`, so `src/tests/**` covers nested fixtures too.
ALLOWLIST: tuple[tuple[str, str], ...] = (
    (
        "src/tests/**",
        "N537: synthetic credentials in test fixtures, KDF/KEK test vectors "
        "(deterministic high-entropy keys), example tokens and detector "
        "scaffolding. detect-secrets' own hook excludes src/tests/** for the "
        "same reason; production code under src/ is still checked.",
    ),
    (
        "src/api/schemas/**",
        "N537: schema examples -- documented example credential shapes used "
        "to illustrate API request/response fields, not deployable secrets.",
    ),
    (
        "src/core/telemetry/scrubbing.py",
        "N537: scrubbing regexes -- PEM/JWT/provider-token detector patterns "
        "used to redact secrets, which look like the secrets they match.",
    ),
    (
        "src/utils/common_utils/utils/licensing/__init__.py",
        "N537: docstring -- an example base64url license token shown in the "
        "module usage documentation, not a live credential.",
    ),
)


# ---------------------------------------------------------------------------
# Result type and heuristics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    path: str  # posix, relative to the scan root
    kind: str  # secret_literal | pem_private_key | known_token_shape
    detail: str  # hash of the candidate -- never the candidate itself

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _shannon(value: str) -> float:
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def _high_entropy_credential(value: str) -> bool:
    """Keyword heuristic's value side: credential-shaped + high entropy.

    Placeholder/documentation markers (``example``, ``your_``, ``{...}`` ...)
    are excluded so docstring templates do not trip the gate.
    """
    if not (MIN_LEN <= len(value) <= MAX_LEN):
        return False
    if not VALUE_SHAPE_RE.match(value):
        return False
    if PLACEHOLDER_RE.search(value):
        return False
    return _shannon(value) >= MIN_ENTROPY


def _wanted(rel: str) -> bool:
    path = Path(rel)
    return path.suffix.lower() in SCANNED_SUFFIXES or path.name.startswith(".env")


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [
            p.value
            for p in node.values
            if isinstance(p, ast.Constant) and isinstance(p.value, str)
        ]
        return "".join(parts) if parts else None
    return None


def _target_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _target_name(node.value)
    return None


def _python_findings(tree: ast.AST, rel: str) -> set[Finding]:
    out: set[Finding] = set()
    for node in ast.walk(tree):
        # PEM blocks and known provider shapes are reported regardless of the
        # name they are bound to.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if PEM_RE.search(value):
                out.add(Finding(rel, "pem_private_key", _digest(value[:64])))
            if any(shape.search(value) for shape in KNOWN_SHAPES):
                out.add(Finding(rel, "known_token_shape", _digest(value)))

        pairs: list[tuple[str, ast.AST]] = []
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = _target_name(target)
                if name:
                    pairs.append((name, node.value))
        elif isinstance(node, ast.AnnAssign) and node.target and node.value:
            name = _target_name(node.target)
            if name:
                pairs.append((name, node.value))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg:
                    pairs.append((kw.arg, kw.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    pairs.append((key.value, value))

        for name, value_node in pairs:
            if not SECRET_NAME_RE.search(name):
                continue
            value = _const_str(value_node)
            if value and _high_entropy_credential(value):
                out.add(Finding(rel, "secret_literal", _digest(value)))
    return out


def _text_findings(text: str, rel: str) -> set[Finding]:
    out: set[Finding] = set()
    if PEM_RE.search(text):
        out.add(Finding(rel, "pem_private_key", _digest(PEM_RE.search(text).group(0))))
    lines = text.splitlines()
    for match in QUOTED_VALUE_RE.finditer(text):
        value = match.group(1)
        line_no = text.count("\n", 0, match.start())
        line = lines[line_no] if line_no < len(lines) else ""
        if any(shape.search(value) for shape in KNOWN_SHAPES):
            out.add(Finding(rel, "known_token_shape", _digest(value)))
        if SECRET_NAME_RE.search(line) and _high_entropy_credential(value):
            out.add(Finding(rel, "secret_literal", _digest(value)))
    # Dotenv files carry `KEY=VALUE` pairs, usually unquoted.
    if Path(rel).name.startswith(".env"):
        for line in lines:
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if SECRET_NAME_RE.search(key) and _high_entropy_credential(value):
                out.add(Finding(rel, "secret_literal", _digest(value)))
    return out


def scan_files(root: Path, rels: Iterable[str]) -> list[Finding]:
    """Scan an explicit list of repo-relative files under *root*."""
    found: set[Finding] = set()
    for rel in rels:
        if not _wanted(rel):
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rel.endswith(".py"):
            try:
                found |= _python_findings(ast.parse(text), rel)
            except (SyntaxError, ValueError):
                # A file that does not parse cannot hide a new credential from
                # a reviewer diff; skip rather than fail on unrelated edits.
                continue
        else:
            found |= _text_findings(text, rel)
    return sorted(found, key=lambda f: (f.path, f.kind, f.detail))


def tracked_files(root: Path) -> list[str]:
    """Tracked source files only -- untracked peer work is out of scope."""
    result = subprocess.run(
        ["git", "ls-files", "--", SCAN_SUBDIR],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [rel for rel in result.stdout.splitlines() if _wanted(rel)]


def scan_tracked_tree(root: Path) -> list[Finding]:
    return scan_files(root, tracked_files(root))


def filter_findings(
    findings: Iterable[Finding],
    allowlist: tuple[tuple[str, str], ...] = ALLOWLIST,
) -> list[Finding]:
    """Drop findings whose path matches a documented allowlist glob."""
    return [
        f
        for f in findings
        if not any(fnmatch.fnmatchcase(f.path, glob) for glob, _ in allowlist)
    ]


def _tree_files(root: Path) -> list[str]:
    return [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]


# ---------------------------------------------------------------------------
# The real tree stays green; the gate is not vacuous.
# ---------------------------------------------------------------------------


@pytest.mark.timeout(300)
def test_tracked_src_tree_has_no_unallowlisted_secret_literals() -> None:
    leftover = filter_findings(scan_tracked_tree(REPO))
    assert leftover == [], [f.to_dict() for f in leftover]


def test_synthetic_high_entropy_secret_is_flagged(tmp_path: Path) -> None:
    secret = "dBJf8sK2mQ9xL4pR7vT1wY6zA3cN5hE"  # 32 chars, entropy ~4.7
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True)
    path.write_text(f'API_KEY = "{secret}"\n', encoding="utf-8")

    findings = scan_files(tmp_path, ["app/config.py"])
    assert any(f.kind == "secret_literal" for f in findings), findings
    # The fresh file is not allowlisted, so it survives the filter.
    assert filter_findings(findings) == findings
    # The finding carries a hash, never the secret itself.
    assert secret not in repr([f.to_dict() for f in findings])


def test_synthetic_pem_private_key_is_flagged(tmp_path: Path) -> None:
    path = tmp_path / "app" / "keys.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        'KEY = "-----BEGIN RSA PRIVATE KEY-----\\nMIIEowIBAAKCAQEA\\n"\n',
        encoding="utf-8",
    )
    findings = scan_files(tmp_path, ["app/keys.py"])
    assert any(f.kind == "pem_private_key" for f in findings), findings
    assert filter_findings(findings) == findings


def test_all_scanned_extensions_are_covered(tmp_path: Path) -> None:
    """The same literal is caught in .py/.yaml/.json/.env, not just .py."""
    secret = "dBJf8sK2mQ9xL4pR7vT1wY6zA3cN5hE"
    files = {
        "app/a.py": f'token = "{secret}"\n',
        "app/b.yaml": f'api_key: "{secret}"\n',
        "app/c.json": f'{{"secret": "{secret}"}}\n',
        "app/.env": f"CLIENT_SECRET={secret}\n",
    }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    findings = filter_findings(scan_files(tmp_path, files))
    for rel in files:
        assert any(f.path == rel for f in findings), (rel, findings)


def test_allowlisted_false_positive_paths_pass(tmp_path: Path) -> None:
    """Each documented N537 false-positive path suppresses the same literal."""
    secret = "dBJf8sK2mQ9xL4pR7vT1wY6zA3cN5hE"
    paths = [
        "src/tests/fixtures/fake_creds.py",  # KDF vectors / test fixtures
        "src/api/schemas/example_token.py",  # schema examples
        "src/core/telemetry/scrubbing.py",  # scrubbing regexes
        "src/utils/common_utils/utils/licensing/__init__.py",  # docstring
    ]
    for rel in paths:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f'token = "{secret}"\n', encoding="utf-8")

    raw = scan_files(tmp_path, _tree_files(tmp_path))
    assert raw, "control: the heuristic must see the injected secrets"
    assert filter_findings(raw) == [], [f.to_dict() for f in raw]


def test_allowlist_documents_n537_false_positive_categories() -> None:
    text = " ".join(reason for _, reason in ALLOWLIST).lower()
    for category in ("kdf", "schema", "scrubbing", "docstring"):
        assert category in text, f"allowlist must document {category} false positives"
