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
test vectors (test fixtures), scrubbing regexes and a credential-looking string
in a licensing docstring.  Test fixtures are waived wholesale, mirroring the
``detect-secrets`` pre-commit exclusion and the SEC100 ``hardcoded_secret``
waiver.  Production code -- including ``src/api/schemas/**`` -- is deliberately
NOT path-exempt: a documented example value there is waived only by the
value-level placeholder heuristic, so a real credential literal committed to a
schema file is still flagged.

Non-vacuity is asserted at the bottom: a synthetic high-entropy literal and a
synthetic PEM block in a fresh temp file are flagged, while the same literal
placed at every allowlisted false-positive path is ignored.  Findings carry a
hash of the secret, never the secret text itself.
"""

from __future__ import annotations

import ast
import base64
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
# A hex literal meeting MIN_LEN is a credential by construction (keys, API
# tokens and digests are routinely hex-encoded).  Shannon entropy over the hex
# alphabet caps at 4.0 bits/char -- a random 32-char hex key averages ~3.6 --
# so a flat Shannon floor of 4.0 silently misses this entire, common class.
# The floor is MIN_LEN (16 hex chars == 64 bits), not 32: a 31-char hex token is
# still 124 bits and must not slip through just because it is one char short of
# the canonical 128-bit encoding.
HEX_SECRET_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
PLACEHOLDER_RE = re.compile(
    r"(?i)example|placeholder|sample|dummy|fake|your|changeme|xxx|todo|"
    r"redacted|insert|replace|deadbeef|\btest\b|[{}]"
)
MIN_LEN, MAX_LEN, MIN_ENTROPY = 16, 512, 4.0

# Shapes that are credentials even at low entropy (e.g. ghp_AAAA...).  Kept as
# a separate signal so an obvious provider prefix is never hidden by a low
# Shannon score.  Cover the alternate AWS key-id prefixes (temporary creds,
# roles, instance profiles, ...) and OpenAI's ``sk-``/``sk-proj-`` spellings as
# well as the literal-prefix guard in check_dangerous_patterns.py does.
# ``eyJ`` catches base64url-encoded JSON credentials such as the example
# license string in a docstring.
KNOWN_SHAPES = tuple(
    re.compile(p)
    for p in (
        r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}",
        r"glpat-[A-Za-z0-9_-]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}",
        r"\b(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|AGPA)[A-Z0-9]{16}\b",
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}",
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


def _is_documentation_placeholder(value: str) -> bool:
    """True for template/example values that should not trip the gate.

    A marker such as ``your_`` or ``example`` is only a placeholder when the
    value is *template-shaped*: braces, or low entropy.  A 24+ char value with
    Shannon entropy >= 4.5 is a random-looking real secret in which a marker
    like ``your``/``xxx`` is a coincidental substring, not a documentation
    cue; suppressing that would hide a live credential (N537 follow-up).
    """
    if not PLACEHOLDER_RE.search(value):
        return False
    if "{" in value or "}" in value:
        return True
    if len(value) >= 24 and _shannon(value) >= 4.5:
        return False
    return True


def _high_entropy_credential(value: str) -> bool:
    """Keyword heuristic's value side: credential-shaped + high entropy.

    Hex-encoded credentials are accepted by shape+length alone because their
    Shannon score is capped below the generic floor.  Everything else needs a
    high Shannon score and must not be a documentation placeholder.
    """
    if not (MIN_LEN <= len(value) <= MAX_LEN):
        return False
    if not VALUE_SHAPE_RE.match(value):
        return False
    # Hex secrets check first: they cannot contain letters outside a-f, and
    # treating them as "high entropy by construction" closes the Shannon gap
    # without lowering the floor for random alphanumeric prose.
    if HEX_SECRET_RE.match(value):
        return True
    if _is_documentation_placeholder(value):
        return False
    return _shannon(value) >= MIN_ENTROPY


def _wanted(rel: str) -> bool:
    path = Path(rel)
    return path.suffix.lower() in SCANNED_SUFFIXES or path.name.startswith(".env")


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------


def _const_str(node: ast.AST) -> str | None:
    """Best-effort static string value of a literal-like expression.

    Adjacent literals (``"a" "b"``) are already folded into one ``Constant`` by
    the parser, but authors can split a committed token just as easily with
    ``+`` or ``.join([...])``.  Only folds when *every* part is static, so a
    runtime/computed value is never guessed at (and never produces a false
    finding).  Non-static f-string interpolations are ignored, as before.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for p in node.values:
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                parts.append(p.value)
            elif isinstance(p, ast.FormattedValue):
                inner = _const_str(p.value)
                if inner is not None:
                    parts.append(inner)
        return "".join(parts) if parts else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _const_str(node.left), _const_str(node.right)
        return left + right if left is not None and right is not None else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
        and node.args
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        parts = []
        for elt in node.args[0].elts:
            part = _const_str(elt)
            if part is None:
                return None
            parts.append(part)
        return node.func.value.value.join(parts)
    # ``b64decode(...).decode()`` wraps the fold in a no-op str conversion.
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "decode"
        and isinstance(node.func.value, ast.Call)
    ):
        return _const_str(node.func.value)
    # A secret kept base64-encoded and decoded at the use site is still a
    # committed credential.  Only fold a single literal argument; a computed
    # input is never decoded or guessed at.
    if isinstance(node, ast.Call) and node.args:
        fn = node.func
        fname = (
            fn.id
            if isinstance(fn, ast.Name)
            else fn.attr
            if isinstance(fn, ast.Attribute)
            else None
        )
        arg = node.args[0]
        if (
            fname == "b64decode"
            and isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
        ):
            try:
                return base64.b64decode(arg.value, validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return None
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
    """Tracked files under ``src/`` only.

    Scope note (N537 follow-up): this intentionally does NOT scan tracked config
    outside ``src/`` (root ``config.yaml``, ``.github/``, ``ci-tooling/``,
    ``docker-compose.yml``, ...).  N537 scanned those with gitleaks and found no
    live secret; widening the path scope here would add prose/config false
    positives.  This guard's claim is limited to "tracked src".
    """
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
    """Each documented N537 false-positive path suppresses the same literal.

    ``src/api/schemas/**`` is intentionally absent: schema files are production
    code and are checked like any other source file (see the dedicated test).
    """
    secret = "dBJf8sK2mQ9xL4pR7vT1wY6zA3cN5hE"
    paths = [
        "src/tests/fixtures/fake_creds.py",  # KDF vectors / test fixtures
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
    for category in ("kdf", "scrubbing", "docstring"):
        assert category in text, f"allowlist must document {category} false positives"
    # A blanket path exemption over production schema code would silently hide
    # any real credential committed there; it must never be allowlisted.
    assert not any(
        fnmatch.fnmatchcase("src/api/schemas/example_token.py", glob)
        for glob, _ in ALLOWLIST
    ), "src/api/schemas/** must not be path-exempt"


def test_high_entropy_secret_in_schema_file_is_flagged(tmp_path: Path) -> None:
    """Non-vacuous: a real secret in a schema file is caught, not suppressed."""
    secret = "dBJf8sK2mQ9xL4pR7vT1wY6zA3cN5hE"
    for rel in (
        "src/api/schemas/auth.py",
        "src/api/schemas/admin/access_tokens.py",
        "src/api/schemas/config.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'api_key = "{secret}"\n', encoding="utf-8")
    findings = filter_findings(scan_files(tmp_path, _tree_files(tmp_path)))
    assert {f.path for f in findings} == {
        "src/api/schemas/auth.py",
        "src/api/schemas/admin/access_tokens.py",
        "src/api/schemas/config.py",
    }, findings


def test_hex_encoded_secret_below_shannon_floor_is_flagged(tmp_path: Path) -> None:
    """A 32-char hex key (entropy ~3.95 < 4.0) must not slip the Shannon floor."""
    hex_secret = "a3f1c9e7b2d408f6a1e53c7d90b24f8e"
    assert _shannon(hex_secret) < MIN_ENTROPY  # guard the premise
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True)
    path.write_text(f'api_key = "{hex_secret}"\n', encoding="utf-8")
    findings = filter_findings(scan_files(tmp_path, ["app/config.py"]))
    assert any(f.kind == "secret_literal" for f in findings), findings


def test_placeholder_substring_in_real_secret_is_flagged(tmp_path: Path) -> None:
    """A marker like ``your`` inside an otherwise-random value is not a template."""
    secret = "dBJf8sK2mQ9xL4yourpR7vT1wY6zA3cN5hE"
    assert PLACEHOLDER_RE.search(secret)  # guard the premise
    assert _shannon(secret) >= 4.5
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True)
    path.write_text(f'token = "{secret}"\n', encoding="utf-8")
    findings = filter_findings(scan_files(tmp_path, ["app/config.py"]))
    assert any(f.kind == "secret_literal" for f in findings), findings


def test_low_entropy_documentation_placeholder_is_still_ignored() -> None:
    """Templates remain exempt: tightening must not flag real doc examples."""
    assert _is_documentation_placeholder("your_api_key_here_000000000000")
    assert not _high_entropy_credential("your_api_key_here_000000000000")


def test_hex_secret_just_below_canonical_32_char_length_is_flagged(
    tmp_path: Path,
) -> None:
    """124 bits of hex is still a secret; the 32-char floor was an off-by-one."""
    hex_secret = "a3f1c9e7b2d408f6a1e53c7d90b24f8"  # 31 chars, ~3.95 bits/char
    assert len(hex_secret) == 31 and _shannon(hex_secret) < MIN_ENTROPY
    assert HEX_SECRET_RE.fullmatch(hex_secret)  # premise: the lowered floor matches
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True)
    path.write_text(f'api_key = "{hex_secret}"\n', encoding="utf-8")
    findings = filter_findings(scan_files(tmp_path, ["app/config.py"]))
    assert any(f.kind == "secret_literal" for f in findings), findings


@pytest.mark.parametrize(
    "token",
    (
        "ASIA" + "A" * 16,  # temporary AWS credentials
        "AROA" + "B" * 16,  # AWS role session
        "sk-proj-" + "c" * 40,  # OpenAI project key
        "sk-" + "d" * 48,  # OpenAI legacy key
    ),
)
def test_alternate_provider_token_prefixes_are_flagged(
    tmp_path: Path, token: str
) -> None:
    """Shape signals are name-agnostic: a generic name must not hide these."""
    path = tmp_path / "app" / "blob.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'value = "{token}"\n', encoding="utf-8")
    findings = scan_files(tmp_path, ["app/blob.py"])
    assert any(f.kind == "known_token_shape" for f in findings), findings


@pytest.mark.parametrize(
    "expression",
    (
        '"dBJf8sK2mQ9xL4pR7" + "vT1wY6zA3cN5hE"',
        '"dBJf8sK2mQ9xL4pR7" + "vT1w" + "Y6zA3cN5hE"',
        '"".join(["dBJf8sK2mQ9xL4pR7", "vT1wY6zA3cN5hE"])',
        '"_".join(("dBJf8sK2mQ9xL4pR7", "vT1wY6zA3cN5hE"))',
        "f\"{'dBJf8sK2mQ9xL4pR7'}vT1wY6zA3cN5hE\"",
    ),
)
def test_secret_built_from_static_fragments_is_flagged(
    tmp_path: Path, expression: str
) -> None:
    """Splitting a literal with +/join/f-string must not defeat the gate."""
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"API_KEY = {expression}\n", encoding="utf-8")
    findings = filter_findings(scan_files(tmp_path, ["app/config.py"]))
    assert any(f.kind == "secret_literal" for f in findings), findings


def test_base64_wrapped_secret_is_flagged(tmp_path: Path) -> None:
    """Decoding at the use site does not make a committed secret legitimate."""
    secret = "dBJf8sK2mQ9xL4pR7vT1wY6zA3cN5hE"
    encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
    assert encoded != secret  # premise
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'import base64\nAPI_KEY = base64.b64decode("{encoded}").decode()\n',
        encoding="utf-8",
    )
    findings = filter_findings(scan_files(tmp_path, ["app/config.py"]))
    assert any(f.kind == "secret_literal" for f in findings), findings
    assert secret not in repr([f.to_dict() for f in findings])


def test_non_static_fragments_are_not_guessed(tmp_path: Path) -> None:
    """The folding helper stays conservative: no finding for runtime values."""
    path = tmp_path / "app" / "config.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        'prefix = "dBJf8sK2mQ9xL4pR7"\n'
        "API_KEY = prefix + get_suffix()\n"
        'API_TOKEN = "".join(parts)\n',
        encoding="utf-8",
    )
    assert scan_files(tmp_path, ["app/config.py"]) == []
