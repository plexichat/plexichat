"""Structural guard for duplicate session verifier mirrors (N712 MRO audit).

The ``SessionMixin`` MRO audit (N712) found that the auth session managers carry
several *duplicate* credential verifier definitions: the same method name is
defined on more than one mixin (``verification.py`` / ``lifecycle.py`` /
``tickets.py``). Because Python MRO picks exactly one of them, a security fix
applied to only one copy silently leaves the other live and weak. Peers have
already had to fix mirrors inconsistently:

* ``3e35a337`` (TicketMixin WS ticket IP/UA binding) vs ``6b4e443c``
  (LifecycleMixin WS ticket mirror);
* ``48b9e9f6`` (VerificationMixin bot owner ceiling) vs ``550f8a32``
  (LifecycleMixin bot verifier ceiling).

This guard pins the *code* of every duplicate verifier in the committed tree,
so neither a marker removed from an existing mirror nor a newly added weak
mirror can regress one of those fixes. It:

1. enumerates - via ``git show <HEAD>:<blob>``, never the working tree - every
   ``_verify_*`` / ``verify_token`` definition in
   ``src/core/auth/managers/session/*.py`` (plus the declared abstract stub in
   ``src/core/auth/managers/protocol.py``);
2. for each duplicated name, requires every *concrete* copy to carry that
   verifier's security markers (bot verifier -> owner ``has_permission`` ceiling
   + fail-closed owner row/payload; WS ticket verifier -> IP/UA blind-index
   ``compare_digest`` + fail-closed binding material + atomic single-use
   consume; session verifier -> type guard + IP/UA binding + expiry; top-level
   ``verify_token`` -> malformed-input fail-closed + rate limit). Pure abstract
   stubs (``...`` / ``pass`` / ``raise NotImplementedError``), such as the
   protocol stub, are allowlisted;
3. fails closed when a new duplicate copy lacks markers, when a marker is
   removed from any copy, or when a duplicated name has no marker spec.

Non-vacuity is established by self-tests: the real committed copies are
accepted, each marker is individually load-bearing (removing it is flagged), a
synthetic weak mirror and a newly added empty mirror are flagged, and the
protocol stub is proven to be detected and excluded (so the allowlist is not a
no-op).
"""

from __future__ import annotations

import ast
import subprocess
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SESSION_DIR = "src/core/auth/managers/session/"
_PROTOCOL_PATH = "src/core/auth/managers/protocol.py"

# Ordered label -> distinctive source marker. Markers are chosen to be exact,
# security-meaningful strings that a stale/stubbed mirror would not contain.
REQUIRED_MARKERS: dict[str, dict[str, str]] = {
    "_verify_bot_token": {
        "owner permission ceiling": "has_permission(owner_perms, perm)",
        "missing-owner fail-closed": "if not owner_row:",
        "malformed owner/bot payload fail-closed": (
            "if not isinstance(owner_perms, dict) or not isinstance(bot_perms, dict):"
        ),
    },
    "_verify_ws_ticket": {
        "type mismatch guard": "Token type mismatch",
        "IP blind-index derivation": (
            'self.crypto.fast_blind_index(ip_address, "ip_address")'
        ),
        "UA blind-index derivation": "self._ua_index(user_agent)",
        "constant-time comparison": "hmac.compare_digest(",
        "missing IP binding material fail-closed": (
            "if not isinstance(stored_ip_index, str) or not stored_ip_index:"
        ),
        "missing UA binding material fail-closed": (
            "if not isinstance(stored_ua_index, str) or not stored_ua_index:"
        ),
        "atomic single-use consume": (
            "UPDATE auth_ws_tickets SET used = 1 WHERE id = ? AND used = 0"
        ),
        "consume rowcount fail-closed": 'getattr(cursor, "rowcount", 0) == 0',
    },
    "_verify_session_token": {
        "token type guard": 'parsed.get("token_type") != "session"',
        "IP blind-index derivation": (
            'self.crypto.fast_blind_index(ip_address, "ip_address")'
        ),
        "UA blind-index derivation": "self._ua_index(user_agent)",
        "constant-time comparison": "hmac.compare_digest(",
        "expiry check": "is_expired(",
        "expiry fail-closed": "TokenExpiredError",
    },
    "_verify_token": {
        # The cached credential dispatcher was duplicated when the mirror's
        # expiry re-check was lifted out of its cache. Both copies must keep
        # dispatching every token type and failing closed on malformed/unknown
        # input, otherwise a mirror can silently stop verifying a credential
        # class (or start trusting a garbage parse).
        "malformed token fail-closed": "if not parsed:",
        "session dispatch": (
            "self._verify_session_token(parsed, ip_address, user_agent=user_agent)"
        ),
        "ticket dispatch": (
            "self._verify_ws_ticket(parsed, ip_address, user_agent=user_agent)"
        ),
        "unknown type fail-closed": 'raise TokenInvalidError("Unknown type")',
    },
    "verify_token": {
        "malformed input fail-closed": "is_malformed_token_input(token)",
        "rate limiter": "check_rate_limit",
        "rate-limit key namespace": "token_verify:",
        # The short-TTL verify cache can serve a verdict whose credential has
        # since expired. Both mirrors must re-check expiry outside the cache so
        # a cached-but-expired token fails closed (N712 parity). Without this
        # marker, a mirror could drop the re-check and drift silently.
        "cache-hit expiry re-check": (
            "expires_at is not None and is_expired(expires_at)"
        ),
    },
}

# The duplicated verifier names the N712 audit identified. Asserting their
# presence keeps enumeration from passing vacuously.
EXPECTED_DUPLICATED = frozenset(REQUIRED_MARKERS)


@dataclass(frozen=True)
class Definition:
    path: str
    line: int
    name: str
    src: str
    stub: bool


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run; pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed_blob(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed blob {path!r}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _committed_session_paths(commit: str) -> list[str]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", _SESSION_DIR)
    assert proc.returncode == 0, (
        f"cannot list committed session dir: {proc.stderr.strip()}"
    )
    return sorted(p for p in proc.stdout.splitlines() if p.endswith(".py"))


def _is_abstract_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the body is only a declaration: ``...`` / ``pass`` / NotImplemented."""
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop a docstring
    if not body:
        return False
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is Ellipsis
        ):
            continue
        if isinstance(stmt, ast.Raise) and stmt.exc is not None:
            exc = stmt.exc
            if (
                isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name)
                and exc.func.id == "NotImplementedError"
            ):
                continue
            if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                continue
        return False
    return True


def _defs_in_blob(path: str, blob: str) -> list[Definition]:
    tree = ast.parse(blob, filename=path)
    lines = blob.splitlines(keepends=True)
    found: list[Definition] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_verify_") or node.name == "verify_token":
            found.append(
                Definition(
                    path=path,
                    line=node.lineno,
                    name=node.name,
                    src="".join(lines[node.lineno - 1 : node.end_lineno]),
                    stub=_is_abstract_stub(node),
                )
            )
    return found


def _collect(commit: str) -> list[Definition]:
    """Enumerate verifier definitions from committed blobs only."""
    defs: list[Definition] = []
    for path in _committed_session_paths(commit):
        defs.extend(_defs_in_blob(path, _committed_blob(commit, path)))
    # Include the declared abstract protocol stub so the allowlist is exercised
    # by the real tree (see test_protocol_stub_is_allowlisted).
    defs.extend(_defs_in_blob(_PROTOCOL_PATH, _committed_blob(commit, _PROTOCOL_PATH)))
    return defs


def _group(defs: list[Definition]) -> dict[str, list[Definition]]:
    by_name: dict[str, list[Definition]] = defaultdict(list)
    for definition in defs:
        by_name[definition.name].append(definition)
    return dict(by_name)


def _check(
    copies_by_name: dict[str, list[Definition]],
) -> tuple[list[str], dict[str, list[Definition]]]:
    """Return ``(problems, duplicated)`` for a name -> copies mapping.

    ``duplicated`` contains only concrete (non-stub) copies of names with at
    least two definitions. Every such name must have a marker spec, and every
    concrete copy must carry every marker.
    """
    problems: list[str] = []
    duplicated: dict[str, list[Definition]] = {}
    for name, copies in copies_by_name.items():
        concrete = [d for d in copies if not d.stub]
        if len(concrete) >= 2:
            duplicated[name] = concrete

    for name, concrete in sorted(duplicated.items()):
        spec = REQUIRED_MARKERS.get(name)
        if spec is None:
            locations = ", ".join(f"{d.path}:{d.line}" for d in concrete)
            problems.append(
                f"NO MARKER SPEC for duplicated verifier {name!r} "
                f"({len(concrete)} committed copies at {locations}); a new "
                "duplicate with no pinned markers fails closed"
            )
            continue
        for definition in concrete:
            missing = [
                label for label, marker in spec.items() if marker not in definition.src
            ]
            if missing:
                problems.append(
                    f"WEAK VERIFIER MIRROR: {name} at "
                    f"{definition.path}:{definition.line} is missing required "
                    f"security marker(s): {', '.join(missing)}"
                )
    return problems, duplicated


def _synthetic(
    name: str,
    src: str,
    *,
    path: str = "<synthetic>",
    line: int = 0,
    stub: bool = False,
) -> Definition:
    return Definition(path=path, line=line, name=name, src=src, stub=stub)


# ---------------------------------------------------------------------------
# Real committed-tree assertions
# ---------------------------------------------------------------------------


def test_enumeration_sees_known_session_files(commit_sha: str) -> None:
    """Enumeration is real: the committed manager files are all present."""
    paths = _committed_session_paths(commit_sha)
    for expected in ("lifecycle.py", "verification.py", "tickets.py"):
        assert any(p.endswith(f"/{expected}") for p in paths), (
            f"committed session enumeration missed {expected!r}: {paths}"
        )
    assert _PROTOCOL_PATH not in paths


def test_committed_duplicate_verifiers_carry_all_markers(commit_sha: str) -> None:
    """Every concrete copy of every duplicated verifier carries its markers."""
    problems, duplicated = _check(_group(_collect(commit_sha)))
    # Non-vacuity: the four N712 duplicates must actually be found and checked.
    assert EXPECTED_DUPLICATED <= duplicated.keys(), (
        f"expected duplicated verifiers {sorted(EXPECTED_DUPLICATED)}; "
        f"enumerated duplicates: {sorted(duplicated)}"
    )
    for name in EXPECTED_DUPLICATED:
        assert len(duplicated[name]) >= 2, f"{name} is not duplicated at HEAD"
    assert not problems, "\n".join(problems)


def test_protocol_stub_is_allowlisted(commit_sha: str) -> None:
    """The abstract protocol stub is detected as a stub and excluded from checks."""
    defs = _collect(commit_sha)
    stubs = [d for d in defs if d.stub]
    assert any(
        d.path == _PROTOCOL_PATH and d.name == "_verify_ws_ticket" for d in stubs
    ), "protocol.py abstract _verify_ws_ticket stub was not detected as a stub"
    concrete_ws = [d for d in defs if d.name == "_verify_ws_ticket" and not d.stub]
    assert len(concrete_ws) == 2, (
        "the allowlisted stub must not be counted as a concrete mirror"
    )


# ---------------------------------------------------------------------------
# Non-vacuity / self-tests of the evaluator itself
# ---------------------------------------------------------------------------


def test_consistent_committed_copies_accepted(commit_sha: str) -> None:
    """Self-test: a set of consistent (real, strong) copies is accepted."""
    by_name = _group(_collect(commit_sha))
    for name in EXPECTED_DUPLICATED:
        problems, duplicated = _check({name: by_name[name]})
        assert name in duplicated
        assert not problems, f"consistent copies of {name} rejected: {problems}"


def test_each_marker_is_load_bearing(commit_sha: str) -> None:
    """Self-test: removing any single marker from one mirror is flagged."""
    by_name = _group(_collect(commit_sha))
    probes = 0
    for name, copies in by_name.items():
        concrete = [d for d in copies if not d.stub]
        if len(concrete) < 2:
            continue
        for label, marker in REQUIRED_MARKERS[name].items():
            target = concrete[0]
            assert marker in target.src, f"probe marker absent from {name}: {label}"
            weakened = replace(target, src=target.src.replace(marker, "NEUTRALIZED"))
            problems, _ = _check({name: [weakened, *concrete[1:]]})
            assert any(label in problem for problem in problems), (
                f"removing marker {label!r} from {name} was not flagged"
            )
            probes += 1
    assert probes >= 12, f"too few marker probes ({probes}) - guard is near-vacuous"


def test_synthetic_weak_mirror_flagged(commit_sha: str) -> None:
    """Self-test: a weak mirror of a strong committed copy is flagged."""
    by_name = _group(_collect(commit_sha))
    strong = [d for d in by_name["_verify_bot_token"] if not d.stub][0]
    weak_src = (
        "def _verify_bot_token(self, parsed, ip_address):\n"
        "    row = self._db.fetch_one('SELECT * FROM auth_bots WHERE id = ?', "
        "(parsed['id'],))\n"
        "    return TokenInfo(permissions=permissions_from_json(row['permissions']))\n"
    )
    weak = _synthetic("_verify_bot_token", weak_src)
    problems, _ = _check({"_verify_bot_token": [strong, weak]})
    assert problems, "synthetic weak bot-token mirror was not flagged"
    assert "owner permission ceiling" in " ".join(problems)
    assert "missing-owner fail-closed" in " ".join(problems)


def test_new_duplicate_missing_markers_fails_closed(commit_sha: str) -> None:
    """Self-test: adding a third, empty duplicate copy fails closed."""
    by_name = _group(_collect(commit_sha))
    concrete = [d for d in by_name["_verify_ws_ticket"] if not d.stub]
    empty = _synthetic(
        "_verify_ws_ticket",
        "def _verify_ws_ticket(self, parsed, ip_address, user_agent=None):\n"
        "    return self.somewhere_else(parsed)\n",
        path="<new-mirror>.py",
        line=1,
    )
    problems, duplicated = _check({"_verify_ws_ticket": [*concrete, empty]})
    assert len(duplicated["_verify_ws_ticket"]) == 3
    assert any("<new-mirror>.py:1" in problem for problem in problems), (
        "a newly added weak duplicate copy was not flagged"
    )


def test_duplicate_without_marker_spec_fails_closed() -> None:
    """Self-test: a brand-new duplicated method name with no spec fails closed."""
    copies = [
        _synthetic(
            "_verify_new_credential",
            "def _verify_new_credential(self): ...",
            stub=False,
        ),
        _synthetic(
            "_verify_new_credential",
            "def _verify_new_credential(self): pass",
            stub=False,
        ),
    ]
    problems, _ = _check({"_verify_new_credential": copies})
    assert any("NO MARKER SPEC" in problem for problem in problems)
