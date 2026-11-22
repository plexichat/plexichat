"""Guard: PlexiSum export/import (encode/decode) size caps stay symmetric.

Background
----------
Three fixes closed the same asymmetry on three codecs:

* N686 ``906e3976`` -- ``encode_container`` refuses a payload larger than
  ``MAX_DECOMPRESSED_SIZE`` (the ceiling ``decode_container`` /
  ``decompress_zstd`` applies on every read).
* N738 ``13e488f7`` -- ``export_xlsx`` delegates to ``_reject_unimportable_export``,
  which applies ``MAX_XLSX_IMPORT_BYTES`` and ``_reject_zip_bomb`` -- the exact
  caps ``import_xlsx`` applies on decode.
* N785 ``6acfc12b`` -- ``export_csv`` compares the rendered text against
  ``MAX_CSV_IMPORT_BYTES``, the ceiling ``import_csv`` applies via
  ``bound_import_text``.

Each fix stops an export that "succeeds" from emitting bytes the very next
import rejects.  The fixes live in committed blobs (HEAD); the working tree is
heavily contested in this workspace, so every read here goes through
``git show <HEAD>:<path>`` -- never a working-tree file.

What this guard does
--------------------
1. Enumerates the three paired codec functions (plus the helper functions whose
   bodies carry the real guards) from a documented registry.
2. Structurally extracts "cap tokens" from each side's committed source via the
   AST: every ``MAX_*`` global name referenced and every size/validation guard
   call (``bound_*``, ``_reject_*``, ``decompress_zstd``).  For each pair the
   read-side caps must be mirrored on the write side (same constant or a
   matching guard call); decode-only helpers are explicit, documented
   one-way paths with reasons.
3. Fails closed on a NEW decode cap: any cap token present on the read side,
   absent from the write side, and not in the documented one-way allowlist is a
   violation.  A future decode-side ceiling therefore cannot be added without
   a matching encode-side guard.
4. Proves non-vacuity with synthetic pairs (symmetric passes; reverted encode
   cap, new decode constant and new decode guard call are all flagged), with a
   live-blob tamper test that strips each real fix's encode-side anchor, and
   with a throwaway repo proving the reader returns the committed blob rather
   than a stale working-tree copy.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class CapSymmetryPair:
    """One encode/decode pair whose size caps must stay symmetric.

    ``decode_functions`` / ``encode_functions`` name the functions whose bodies
    are unioned to form each side (helpers that carry the real guard are
    included explicitly).  ``critical_caps`` are the cap tokens that MUST be
    present on both sides -- the exact tokens the fixing commit added.
    ``decode_only`` maps a decode-side token to the reason it legitimately has
    no encode-side mirror (every entry is a documented one-way path).
    ``encode_anchor`` is the single token removed by the live tamper test.
    """

    key: str
    path: str
    decode_functions: tuple[str, ...]
    encode_functions: tuple[str, ...]
    critical_caps: frozenset[str]
    decode_only: Mapping[str, str] = field(default_factory=dict)
    encode_anchor: str = ""


CAP_SYMMETRY_PAIRS: tuple[CapSymmetryPair, ...] = (
    CapSymmetryPair(
        key="n686-container-max-decompressed-size",
        path="src/core/suite/sum/container.py",
        decode_functions=("decode_container", "decompress_zstd"),
        encode_functions=("encode_container",),
        critical_caps=frozenset({"MAX_DECOMPRESSED_SIZE"}),
        decode_only={
            "decompress_zstd": (
                "decode streams zstd under the 64 MiB ceiling (bomb guard); "
                "encode enforces the identical MAX_DECOMPRESSED_SIZE directly "
                "on the raw payload before compressing, so it never needs to "
                "decompress"
            ),
        },
        encode_anchor="MAX_DECOMPRESSED_SIZE",
    ),
    CapSymmetryPair(
        key="n785-csv-import-byte-cap",
        path="src/core/suite/sum/manager_data.py",
        decode_functions=("import_csv",),
        encode_functions=("export_csv",),
        critical_caps=frozenset({"MAX_CSV_IMPORT_BYTES"}),
        decode_only={
            "bound_import_text": (
                "import bounds decoded text through this helper with "
                "max_bytes=MAX_CSV_IMPORT_BYTES; export enforces the identical "
                "byte ceiling inline (len(content.encode('utf-8')) > "
                "MAX_CSV_IMPORT_BYTES)"
            ),
            "MAX_DECOMPRESSED_SIZE": (
                "the chunked import path refuses a blob past the container "
                "ceiling; the matching encode guard is encode_container (the "
                "container pair), which every published blob passes through"
            ),
            "bound_cell_value": (
                "import normalises cell values on write; export renders stored "
                "values that already passed this choke point at write time"
            ),
            "bound_workspace_json": (
                "import normalises cell style JSON on write; export only "
                "reads already-persisted, bounded style data"
            ),
        },
        encode_anchor="MAX_CSV_IMPORT_BYTES",
    ),
    CapSymmetryPair(
        key="n738-xlsx-import-byte-cap",
        path="src/core/suite/sum/manager_exchange.py",
        decode_functions=("import_xlsx", "_reject_zip_bomb"),
        encode_functions=(
            "export_xlsx",
            "_reject_unimportable_export",
            "_reject_zip_bomb",
        ),
        critical_caps=frozenset({"MAX_XLSX_IMPORT_BYTES", "_reject_zip_bomb"}),
        decode_only={
            "bound_import_bytes": (
                "import bounds decoded bytes through this helper with "
                "max_bytes=MAX_XLSX_IMPORT_BYTES; the export mirror applies "
                "the identical ceiling inline in _reject_unimportable_export"
            ),
            "bound_formula_text": (
                "import re-bounds formula text from the decoded file; export "
                "writes formulas already bounded at the operation write path"
            ),
        },
        encode_anchor="MAX_XLSX_IMPORT_BYTES",
    ),
)

# Tie each registry entry to the commit that introduced it, so the enumeration
# cannot silently drift away from the three fixes it claims to verify.
PAIR_KEY_BY_COMMIT: Mapping[str, str] = {
    "906e3976": "n686-container-max-decompressed-size",
    "13e488f7": "n738-xlsx-import-byte-cap",
    "6acfc12b": "n785-csv-import-byte-cap",
}

# Guard-call prefixes recognised as cap/size guards.  Deliberately broad: a
# *new* decode-side guard (e.g. a future ``_reject_*`` helper) is discovered and
# fails closed unless it is mirrored or explicitly allowlisted.
_GUARD_PREFIXES = ("bound_", "_reject_")
_GUARD_EXACT = frozenset({"decompress_zstd"})


def _git(*args: str, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
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


def _read_blob(commit: str, path: str, cwd: Path = _REPO_ROOT):
    """Return a thunk yielding ``<commit>:<path>``; never read the worktree."""

    def read() -> str:
        proc = _git("show", f"{commit}:{path}", cwd=cwd)
        if proc.returncode != 0:
            raise AssertionError(
                f"blob {path!r} missing from {commit}: {proc.stderr.strip()}"
            )
        return proc.stdout

    return read


def _index_functions(
    source: str,
) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Index every function/method definition in ``source`` by its name."""
    tree = ast.parse(source)
    index: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            index.setdefault(node.name, []).append(node)
    return index


def _call_target_name(func: ast.expr) -> str | None:
    """Return the bare name of a call target (``self._x`` and ``_x`` both -> ``_x``)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_guard_call(name: str) -> bool:
    return name.startswith(_GUARD_PREFIXES) or name in _GUARD_EXACT


def _cap_tokens(
    index: Mapping[str, list[ast.FunctionDef | ast.AsyncFunctionDef]],
    names: tuple[str, ...],
) -> set[str]:
    """Union the cap tokens referenced anywhere in the named functions.

    A cap token is either a ``MAX_*`` global constant name or a call to a
    size/validation guard helper.  Using the committed source AST (not prose)
    is what makes the enumeration structural rather than a self-declaration.
    """
    tokens: set[str] = set()
    for name in names:
        for node in index.get(name, ()):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id.startswith("MAX_"):
                    tokens.add(sub.id)
                elif isinstance(sub, ast.Call):
                    target = _call_target_name(sub.func)
                    if target and _is_guard_call(target):
                        tokens.add(target)
    return tokens


def _evaluate_pair(pair: CapSymmetryPair, source: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for one pair against one committed source blob."""
    index = _index_functions(source)
    wanted = set(pair.decode_functions) | set(pair.encode_functions)
    missing = sorted(wanted - set(index))
    if missing:
        return False, (
            f"[{pair.key}] MISSING FUNCTION(S) in committed blob {pair.path!r}: {missing}"
        )

    decode = _cap_tokens(index, pair.decode_functions)
    encode = _cap_tokens(index, pair.encode_functions)

    for cap in sorted(pair.critical_caps):
        if cap not in decode:
            return False, (
                f"[{pair.key}] CRITICAL CAP NOT FOUND ON DECODE SIDE: {cap!r} "
                f"in {pair.path!r} ({pair.decode_functions})"
            )
    for cap in sorted(pair.critical_caps):
        if cap not in encode:
            return False, (
                f"[{pair.key}] CAP SYMMETRY VIOLATION: decode cap {cap!r} has no "
                f"encode-side guard in {pair.path!r} ({pair.encode_functions})"
            )

    allowlisted = set(pair.decode_only)
    unmirrored = sorted(decode - encode - allowlisted)
    if unmirrored:
        return False, (
            f"[{pair.key}] UNMIRRORED DECODE CAP(S) with no matching encode guard "
            f"and no allowlist reason: {unmirrored} (decode-only caps were "
            f"{sorted(decode - encode)})"
        )
    stale = sorted(allowlisted - decode)
    if stale:
        return False, (
            f"[{pair.key}] STALE DECODE-ONLY ALLOWLIST entries not present on the "
            f"decode side: {stale}"
        )

    return True, (
        f"[{pair.key}] symmetric: decode caps {sorted(decode)}, encode caps "
        f"{sorted(encode)}, one-way {sorted(allowlisted)}"
    )


@pytest.mark.parametrize(
    "pair", CAP_SYMMETRY_PAIRS, ids=[p.key for p in CAP_SYMMETRY_PAIRS]
)
def test_export_import_cap_symmetry_at_head(
    pair: CapSymmetryPair, commit_sha: str
) -> None:
    """Each documented codec pair's caps are symmetric in the committed tree."""
    source = _read_blob(commit_sha, pair.path)()
    ok, detail = _evaluate_pair(pair, source)
    assert ok, detail


def test_registry_is_well_formed() -> None:
    """The pair registry is non-empty, unique and fully documented."""
    keys = [p.key for p in CAP_SYMMETRY_PAIRS]
    assert keys, "cap symmetry registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate cap symmetry pair keys"
    for pair in CAP_SYMMETRY_PAIRS:
        assert pair.path.startswith("src/"), pair.key
        assert pair.decode_functions and pair.encode_functions, pair.key
        assert pair.critical_caps, f"{pair.key} has no critical caps (vacuous)"
        assert pair.encode_anchor in pair.critical_caps, pair.key
        assert pair.encode_anchor, pair.key
        for token, reason in pair.decode_only.items():
            assert token, pair.key
            assert reason, f"{pair.key}: one-way path {token!r} lacks a reason"


def test_registry_commits_are_real_fix_ancestors(commit_sha: str) -> None:
    """Non-vacuity: every pair ties to a real ancestor fixing commit."""
    keys = {p.key for p in CAP_SYMMETRY_PAIRS}
    assert set(PAIR_KEY_BY_COMMIT.values()) == keys, (
        "PAIR_KEY_BY_COMMIT does not cover exactly the cap symmetry registry"
    )
    for sha, key in sorted(PAIR_KEY_BY_COMMIT.items()):
        resolved = _git("rev-parse", "--verify", f"{sha}^{{commit}}")
        assert resolved.returncode == 0, f"{key} names unknown commit {sha}"
        anc = _git("merge-base", "--is-ancestor", sha, commit_sha)
        assert anc.returncode == 0, f"{key} commit {sha} is not an ancestor of HEAD"


def test_critical_caps_are_structurally_present_in_head_blobs(
    commit_sha: str,
) -> None:
    """The critical caps are extracted from real code, not merely declared."""
    for pair in CAP_SYMMETRY_PAIRS:
        index = _index_functions(_read_blob(commit_sha, pair.path)())
        decode = _cap_tokens(index, pair.decode_functions)
        encode = _cap_tokens(index, pair.encode_functions)
        assert pair.critical_caps <= decode, (
            f"{pair.key}: declared caps {sorted(pair.critical_caps - decode)} not "
            "found on the real decode side"
        )
        assert pair.critical_caps <= encode, (
            f"{pair.key}: declared caps {sorted(pair.critical_caps - encode)} not "
            "found on the real encode side"
        )


class _RenameTokens(ast.NodeTransformer):
    """Rename cap-token names/attributes to a sentinel within one AST subtree."""

    def __init__(self, tokens: frozenset[str]) -> None:
        self.tokens = tokens

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        if node.id in self.tokens:
            return ast.Name(id=f"REMOVED_{node.id}", ctx=node.ctx)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        if node.attr in self.tokens:
            return ast.Attribute(
                value=node.value, attr=f"REMOVED_{node.attr}", ctx=node.ctx
            )
        return node


def _strip_encode_token(source: str, pair: CapSymmetryPair) -> str:
    """Simulate reverting a fix: erase one anchor token from the encode side only."""
    tree = ast.parse(source)
    tokens = frozenset({pair.encode_anchor})
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in pair.encode_functions
        ):
            _RenameTokens(tokens).visit(node)
    return ast.unparse(tree)


@pytest.mark.parametrize(
    "pair", CAP_SYMMETRY_PAIRS, ids=[p.key for p in CAP_SYMMETRY_PAIRS]
)
def test_reverting_each_real_encode_guard_is_flagged(
    pair: CapSymmetryPair, commit_sha: str
) -> None:
    """Non-vacuity on real blobs: removing each fix's encode guard fails."""
    source = _read_blob(commit_sha, pair.path)()
    ok, detail = _evaluate_pair(pair, source)
    assert ok, detail  # baseline: the real committed pair is symmetric

    tampered = _strip_encode_token(source, pair)
    ok, detail = _evaluate_pair(pair, tampered)
    assert not ok, f"{pair.key}: reverting encode guard was not detected"
    assert "CAP SYMMETRY VIOLATION" in detail
    assert pair.encode_anchor in detail


# --- Synthetic self-tests: the evaluator is falsifiable on both sides -------

_SYNTHETIC_PAIR = CapSymmetryPair(
    key="synthetic",
    path="synthetic.py",
    decode_functions=("decode",),
    encode_functions=("encode",),
    critical_caps=frozenset({"MAX_SYNC_CAP"}),
)


def _synth_source(decode_body: str, encode_body: str) -> str:
    return (
        "MAX_SYNC_CAP = 100\n"
        "MAX_NEW_DECODE_CAP = 200\n\n"
        "def decode(raw):\n" + decode_body + "\n\n"
        "def encode(raw):\n" + encode_body + "\n"
    )


_SYMMETRIC_BODY = (
    '    if len(raw) > MAX_SYNC_CAP:\n        raise ValueError("too big")\n'
)


def test_synthetic_symmetric_pair_passes() -> None:
    """Control: a genuinely symmetric pair is accepted (not fail-always)."""
    source = _synth_source(_SYMMETRIC_BODY, _SYMMETRIC_BODY)
    ok, detail = _evaluate_pair(_SYNTHETIC_PAIR, source)
    assert ok, detail


def test_synthetic_asymmetric_pair_is_flagged() -> None:
    """A symmetric-on-decode / missing-on-encode cap pair is rejected."""
    encode_body = (
        '    if len(raw) > MAX_OTHER_CAP:\n        raise ValueError("too big")\n'
    )
    source = _synth_source(_SYMMETRIC_BODY, encode_body)
    ok, detail = _evaluate_pair(_SYNTHETIC_PAIR, source)
    assert not ok
    assert "CAP SYMMETRY VIOLATION" in detail and "MAX_SYNC_CAP" in detail


def test_synthetic_new_decode_constant_fails_closed() -> None:
    """A NEW decode cap with no encode-side guard is flagged."""
    decode_body = _SYMMETRIC_BODY + (
        "    if len(raw) > MAX_NEW_DECODE_CAP:\n"
        '        raise ValueError("new ceiling")\n'
    )
    source = _synth_source(decode_body, _SYMMETRIC_BODY)
    ok, detail = _evaluate_pair(_SYNTHETIC_PAIR, source)
    assert not ok
    assert "UNMIRRORED DECODE CAP" in detail and "MAX_NEW_DECODE_CAP" in detail


def test_synthetic_new_decode_guard_call_fails_closed() -> None:
    """A NEW decode-side guard call with no encode mirror is flagged."""
    decode_body = (
        "    bound_import_bytes(raw, max_bytes=MAX_SYNC_CAP)\n" + _SYMMETRIC_BODY
    )
    source = _synth_source(decode_body, _SYMMETRIC_BODY)
    ok, detail = _evaluate_pair(_SYNTHETIC_PAIR, source)
    assert not ok
    assert "UNMIRRORED DECODE CAP" in detail and "bound_import_bytes" in detail


def test_synthetic_documented_one_way_path_is_allowed() -> None:
    """A decode-only guard with a documented reason is accepted."""
    pair = CapSymmetryPair(
        key="synthetic-one-way",
        path="synthetic.py",
        decode_functions=("decode",),
        encode_functions=("encode",),
        critical_caps=frozenset({"MAX_SYNC_CAP"}),
        decode_only={
            "bound_import_bytes": "import decodes through a helper; export checks inline"
        },
    )
    decode_body = (
        "    bound_import_bytes(raw, max_bytes=MAX_SYNC_CAP)\n" + _SYMMETRIC_BODY
    )
    ok, detail = _evaluate_pair(pair, _synth_source(decode_body, _SYMMETRIC_BODY))
    assert ok, detail


def test_synthetic_stale_one_way_allowlist_is_flagged() -> None:
    """An allowlist entry that is not on the decode side cannot mask a pass."""
    pair = CapSymmetryPair(
        key="synthetic-stale-allowlist",
        path="synthetic.py",
        decode_functions=("decode",),
        encode_functions=("encode",),
        critical_caps=frozenset({"MAX_SYNC_CAP"}),
        decode_only={"bound_formula_text": "no longer used on decode"},
    )
    ok, detail = _evaluate_pair(pair, _synth_source(_SYMMETRIC_BODY, _SYMMETRIC_BODY))
    assert not ok
    assert "STALE DECODE-ONLY ALLOWLIST" in detail and "bound_formula_text" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(
    tmp_path: Path,
) -> None:
    """Non-vacuity: the reader really reads the commit, not a working file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "fix.py"
    target.write_text("MAX_SYNC_CAP = 100\n", encoding="utf-8")
    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "fix.py", cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-qm",
        "fix",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()

    # Peer-style stale working copy: the fix is changed on disk only.
    target.write_text("MAX_SYNC_CAP = 1  # stale\n", encoding="utf-8")
    assert "stale" in target.read_text(encoding="utf-8")

    # The same reader used by the guard still sees the committed value.
    committed_source = _read_blob(sha, "fix.py", cwd=repo)()
    assert "MAX_SYNC_CAP = 100" in committed_source
    assert "stale" not in committed_source
