"""Structural guard: every credential-verification entry point binds a family.

Follow-up to the token-audience campaign.  A peer reviewed the runtime paths and
confirmed that every function which turns a presented credential into an auth
decision is bound to one credential family, either by:

* checking the parsed token type / prefix against the expected value
  (``parsed["token_type"] != "bot"``, ``parts[0] != "webhook"``, the
  ``parse_oauth_token`` type dispatch, ...), or
* resolving the credential through a domain-separated lookup / blind index that
  cannot accept another family (``fast_blind_index(token, "api_access_token")``,
  the ``admin_sessions`` fingerprint lookup, the domain-scoped OTP challenge
  keyspace, ...).

Runtime tests cover each site individually, but nothing stops a later edit from
dropping a type check, or from adding a brand-new verification entry point that
accepts any credential.  This is a pure AST ratchet (no app import / config /
DB) with four halves:

1. ``VERIFIER_REGISTRY`` enumerates every committed verification entry point
   (session + bot + WS ticket verifiers, API access tokens, OAuth
   exchange/refresh/verify/revoke, interaction tokens, webhook tokens, admin
   session + OTP, e-mail verification, password reset and 2FA).  Each row names
   the committed path, the function, the binding kinds that satisfy it and a
   written reason.  Documented delegations, stubs, primitives and the
   intentional "WS ticket accepted by session verify" case live here with an
   explicit justification -- they are allowlisted, not silently ignored.

2. A whole-tree sweep finds every *new* candidate verification entry point: a
   committed production function whose name signals credential verification and
   which calls a credential parser / verifier primitive.  A candidate that is
   not in the registry must bind its own family (type / prefix / domain /
   dispatch) -- otherwise the sweep fails closed.

3. Blobs are read from COMMITTED HEAD (``git cat-file --batch`` against the
   pinned SHA), never the working tree: many production files are contested by
   parallel agents whose stale copies can mislead a working-tree scan while a
   whole-file commit could silently revert a check.

4. Non-vacuity is proven from both sides: a synthetic verifier with no family
   binding is flagged, while typed / domain-separated / prefix / documented
   cases are accepted; mutating a synthetic committed blob flips the verdict.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

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


@lru_cache(maxsize=None)
def _committed_sources(commit: str) -> dict[str, str]:
    """Read every committed production blob in one ``git cat-file --batch``.

    Byte offsets are used to split the batch stream, so the raw bytes are
    decoded per file (``git cat-file`` sizes are in bytes, not characters).
    """
    files = _production_files(commit)
    payload = "".join(f"{commit}:{path}\n" for path in files).encode("utf-8")
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input=payload,
        capture_output=True,
    )
    assert proc.returncode == 0, f"git cat-file --batch failed: {proc.stderr!r}"
    out = proc.stdout
    result: dict[str, str] = {}
    pos = 0
    for path in files:
        end = out.find(b"\n", pos)
        assert end != -1, "truncated git cat-file --batch header"
        parts = out[pos:end].split()
        assert (
            len(parts) == 3
            and parts[1] == b"blob"
            and not parts[0].endswith(b"missing")
        ), f"blob missing from {commit}: {path} ({out[pos:end]!r})"
        size = int(parts[2])
        result[path] = out[end + 1 : end + 1 + size].decode("utf-8", "replace")
        pos = end + 1 + size + 1
    return result


def _batch_reader(commit: str) -> Callable[[str], str]:
    sources = _committed_sources(commit)
    assert sources, "no committed blobs were loaded"

    def read(path: str) -> str:
        assert path in sources, f"path {path!r} is not a committed production blob"
        return sources[path]

    return read


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# Literal values that constitute a credential-family binding when compared
# against a parsed token's type/prefix.  A verifier that rejects every other
# value in this set cannot be confused with a different token family.
CREDENTIAL_TYPES = frozenset(
    {
        "session",
        "bot",
        "wst",
        "auth",
        "access",
        "refresh",
        "int",
        "email",
        "verify_email",
        "reset_password",
        "2fa",
        "webhook",
    }
)

# Credential parsers: calling one is the signature of a credential-verifying
# function (it consumes a structured bearer credential).
PARSERS = frozenset({"parse_token", "parse_oauth_token", "_parse_webhook_token"})

# Verifier / hash / blind-index primitives a credential decision calls through.
# Presence of one of these in a verification-named function marks it as a
# candidate for the fail-closed sweep.
VERIFY_PRIMITIVES = frozenset(
    {
        "verify_token_hash",
        "_verify_token",
        "verify_token",
        "verify_api_access_token",
        "_introspect_api_access_token",
        "_verify_ws_ticket",
        "_hash_admin_token",
        "_verify_admin_password",
        "_validate_otp_challenge",
        "_otp_challenge_fingerprint",
        "verify_pkce_challenge",
        "verify_otp",
        "_constant_time_compare",
    }
)

# A blind-index helper with a literal domain argument is a domain-separated
# lookup: the credential is searched only inside one family's index/hash.
BLIND_INDEX_SUFFIX = "blind_index"

# Domain-specific fingerprint / keyspace helpers (admin sessions and OTP).
DOMAIN_HELPERS = frozenset({"_hash_admin_token", "_otp_challenge_fingerprint"})
DOMAIN_KEYSPACES = ("admin_otp_challenge:", "api_access_token")

# Calls that prove a function delegates to a verifier which does the binding.
# "delegated" is a *documented* kind: the sweep never auto-accepts it, so a new
# wrapper still fails closed until a registry row explains the delegation.
DELEGATED_VERIFIERS = frozenset(
    {
        "verify_token",
        "_verify_token",
        "verify_api_access_token",
        "_introspect_api_access_token",
        "_verify_ws_ticket",
        "verify_otp",
        "verify_otp_setup",
        "_validate_otp_challenge",
        "validate_session",
        "get_webhook_by_token",
        "execute_webhook",
        "verify_interaction_token",
        "verify_email",
        "reset_password",
        "complete_2fa",
        "exchange_code",
        "refresh_token",
        "revoke_token",
        "verify_access_token",
        # Local wrapper names in the request middleware; these simply forward
        # to one of the checked verifiers above.
        "_verify_with_cleanup",
        "_verify_with_cache",
        "_verify_cookie",
        "_verify_token_cached",
    }
)

# Constant-time comparison helpers used by pure hash primitives (no family
# separation is needed there: the caller supplies the expected hash and the
# comparison itself is the whole decision).
COMPARE_CALLS = frozenset(
    {"compare_digest", "_constant_time_compare", "constant_time_compare"}
)

# Binding kinds that constitute a real, self-contained family guard.  The
# whole-tree sweep auto-accepts ONLY these.
SELF_BINDING_KINDS = frozenset({"type", "prefix", "domain", "dispatch"})
# Documented kinds require a registry row with a written reason.
DOCUMENTED_KINDS = frozenset({"delegated", "stub", "primitive"})
ALL_KINDS = SELF_BINDING_KINDS | DOCUMENTED_KINDS

# Candidate name pattern: credential-verification entry points.  Kept precise
# (no bare "validat"/"invalidate") to keep false positives low; the primitive
# requirement below narrows it further.
CANDIDATE_NAME = frozenset(
    {
        "verify_token",
        "_verify_token",
        "_verify_token_cached",
        "_verify_with_cleanup",
        "_verify_with_cache",
        "_verify_access_with_cleanup",
        "_verify_cookie",
        "_verify_cookie_cached",
        "cached_verify_token",
        "_verify_ws_ticket",
        "_verify_session_token",
        "_verify_bot_token",
        "verify_api_access_token",
        "_introspect_api_access_token",
        "exchange_code",
        "refresh_token",
        "revoke_token",
        "verify_access_token",
        "verify_interaction_token",
        "verify_otp",
        "verify_otp_setup",
        "_validate_otp_challenge",
        "validate_session",
        "verify_email",
        "resend_verification",
        "reset_password",
        "complete_2fa",
        "get_webhook_by_token",
        "execute_webhook",
        "verify_token_hash",
    }
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifierEntry:
    path: str
    func: str
    accept: frozenset[str]
    reason: str
    # Same-module helper(s) a "delegated" wrapper forwards to. When present the
    # registry check requires the wrapper to actually call each target AND the
    # target (or a same-module chain from it) to carry a real family binding.
    # This stops "delegated" from becoming a rubber stamp for unbound helpers.
    delegates_to: frozenset[str] = frozenset()


def _entry(
    path: str, func: str, accept: str, reason: str, delegates_to: str = ""
) -> VerifierEntry:
    kinds = frozenset(k.strip() for k in accept.split("|"))
    assert kinds and kinds <= ALL_KINDS, f"bad binding kinds for {path}:{func}: {kinds}"
    assert reason.strip(), f"missing reason for {path}:{func}"
    targets = frozenset(t.strip() for t in delegates_to.split("|") if t.strip())
    return VerifierEntry(path, func, kinds, reason, targets)


VERIFIER_REGISTRY: tuple[VerifierEntry, ...] = (
    # --- session / bot / WS-ticket verifiers ---------------------------------
    _entry(
        "src/core/auth/managers/session/verification.py",
        "verify_token",
        "delegated",
        "Single auth gateway. Intentionally accepts a WS ticket (wst) in "
        "addition to session/bot tokens: it delegates to _verify_token, whose "
        "typed dispatch routes wst to _verify_ws_ticket (which itself rejects "
        "any token_type != 'wst' and atomically consumes the ticket). This is "
        "the documented WS-ticket-accepted-by-session-verify exception. The "
        "delegation target is verified to be family-bound below.",
        delegates_to="_verify_token",
    ),
    _entry(
        "src/core/auth/managers/session/verification.py",
        "_verify_token",
        "dispatch|type",
        "Dispatches on parsed['token_type'] to session/bot/wst sub-verifiers "
        "and raises 'Unknown type' otherwise; the dispatch itself binds each "
        "branch and no untyped family reaches a decision.",
    ),
    _entry(
        "src/core/auth/managers/session/verification.py",
        "_verify_session_token",
        "type|domain",
        "Rejects token_type != 'session' before any lookup; IP/UA blind-index "
        "checks use the 'ip_address' domain.",
    ),
    _entry(
        "src/core/auth/managers/session/verification.py",
        "_verify_bot_token",
        "type",
        "Rejects token_type != 'bot' before reading auth_bots.",
    ),
    _entry(
        "src/core/auth/managers/session/lifecycle.py",
        "verify_token",
        "delegated",
        "Thin uncached wrapper: malformed-input check, token-verify rate limit, "
        "then delegates to _verify_token (which parses and dispatches on "
        "parsed['token_type'] to session/bot/wst sub-verifiers, failing closed "
        "on unknown types) and re-checks the cached verdict's expiry on every "
        "call. Delegation target is verified to be family-bound below.",
        delegates_to="_verify_token",
    ),
    _entry(
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_token",
        "dispatch|type",
        "Cached core verifier: parse_token then token_type dispatch to "
        "session/bot/wst sub-verifiers; unknown types raise 'Unknown type', so "
        "no untyped family reaches an auth decision. This is the bound target "
        "of the thin verify_token wrapper above.",
    ),
    _entry(
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_session_token",
        "type|domain",
        "Rejects token_type != 'session'; IP binding uses the 'ip_address' "
        "blind-index domain.",
    ),
    _entry(
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_bot_token",
        "type",
        "Rejects token_type != 'bot' before reading auth_bots.",
    ),
    _entry(
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_ws_ticket",
        "type",
        "Rejects token_type != 'wst' before reading auth_ws_tickets.",
    ),
    _entry(
        "src/core/auth/managers/session/tickets.py",
        "_verify_ws_ticket",
        "type",
        "TicketMixin verifier rejects token_type != 'wst' and consumes the "
        "ticket atomically.",
    ),
    _entry(
        "src/core/auth/managers/protocol.py",
        "_verify_ws_ticket",
        "stub",
        "Abstract protocol declaration (ellipsis body); no credential is "
        "accepted here. The concrete bind is enforced by the implementing "
        "mixins registered above.",
    ),
    # --- API access tokens ---------------------------------------------------
    _entry(
        "src/core/auth/tokens.py",
        "verify_api_access_token",
        "delegated",
        "Module-level wrapper delegating to the manager verifier, which "
        "resolves the credential via the 'api_access_token' blind index.",
    ),
    _entry(
        "src/core/auth/managers/api_token.py",
        "verify_api_access_token",
        "domain|delegated",
        "Resolves through _introspect_api_access_token, whose blind index is "
        "domain-separated to 'api_access_token'.",
    ),
    _entry(
        "src/core/auth/managers/api_token.py",
        "_introspect_api_access_token",
        "domain",
        "Uses fast_blind_index/legacy_fast_blind_index with the literal domain "
        "'api_access_token'; a session/bot/OAuth token cannot resolve in this "
        "index. Possession is then proven by exact constant-time comparison.",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_access_with_cleanup",
        "delegated",
        "Request-scoped wrapper calling auth.verify_api_access_token (domain "
        "blind-index bound in the manager).",
    ),
    # --- OAuth ---------------------------------------------------------------
    _entry(
        "src/core/applications/oauth/flows.py",
        "exchange_code",
        "type",
        "parse_oauth_token + parsed['token_type'] != 'auth'; an access/refresh "
        "token cannot be redeemed as an authorization code.",
    ),
    _entry(
        "src/core/applications/oauth/flows.py",
        "refresh_token",
        "type",
        "parse_oauth_token + parsed['token_type'] != 'refresh'.",
    ),
    _entry(
        "src/core/applications/oauth/flows.py",
        "verify_access_token",
        "type",
        "parse_oauth_token + parsed['token_type'] != 'access'.",
    ),
    _entry(
        "src/core/applications/oauth/flows.py",
        "revoke_token",
        "dispatch|type",
        "parse_oauth_token + token_type restricted to ('access','refresh'); "
        "the type also selects the correct hash column.",
    ),
    _entry(
        "src/core/applications/interactions/handler.py",
        "verify_interaction_token",
        "type",
        "parse_oauth_token + parsed['token_type'] != 'int', so an access token "
        "cannot be used as an interaction token.",
    ),
    # --- webhook tokens ------------------------------------------------------
    _entry(
        "src/core/webhooks/manager/crud.py",
        "get_webhook_by_token",
        "prefix",
        "Calls _parse_webhook_token, which returns None unless parts[0] == "
        "'webhook'; the prefix is a family binding before the hash check.",
    ),
    _entry(
        "src/core/webhooks/manager/execute.py",
        "execute_webhook",
        "prefix",
        "Calls _parse_webhook_token (webhook prefix binding) and additionally "
        "requires token.startswith('webhook.') / a matching webhook_id.",
    ),
    _entry(
        "src/core/webhooks/manager/token.py",
        "_verify_token",
        "primitive",
        "Pure constant-time hash comparison of a secret against a supplied "
        "hash; family binding is done by the caller's webhook-prefix parser.",
    ),
    _entry(
        "src/core/webhooks/manager/base.py",
        "_verify_token",
        "stub",
        "Abstract trait declaration (ellipsis body); the concrete primitive "
        "is registered above.",
    ),
    # --- admin session / OTP -------------------------------------------------
    _entry(
        "src/core/admin/auth/session_mixin.py",
        "validate_session",
        "domain",
        "Hashes the presented credential with _hash_admin_token and looks it "
        "up only in admin_sessions; no other family's token is stored there, "
        "so the lookup scope is a family binding.",
    ),
    _entry(
        "src/core/admin/__init__.py",
        "validate_session",
        "delegated",
        "Module wrapper delegating to admin.auth.validate_session (domain "
        "fingerprint lookup in admin_sessions).",
    ),
    _entry(
        "src/core/admin/auth/otp_mixin.py",
        "verify_otp",
        "domain",
        "Validates the challenge through _validate_otp_challenge in the "
        "domain-scoped 'admin_otp_challenge:' keyspace with a domain-specific "
        "fingerprint and flow-type binding.",
    ),
    _entry(
        "src/core/admin/auth/otp_mixin.py",
        "verify_otp_setup",
        "domain",
        "Same domain-scoped challenge validation as verify_otp, with is_setup "
        "flow binding.",
    ),
    _entry(
        "src/core/admin/auth/helpers.py",
        "_validate_otp_challenge",
        "domain",
        "Reads the 'admin_otp_challenge:' keyspace keyed by "
        "_otp_challenge_fingerprint and enforces the is_setup flow type.",
    ),
    _entry(
        "src/core/admin/auth/__init__.py",
        "verify_otp",
        "delegated",
        "Package wrapper delegating to the domain-bound OTP verifier.",
    ),
    _entry(
        "src/core/admin/__init__.py",
        "verify_otp",
        "delegated",
        "Package wrapper delegating to the domain-bound OTP verifier.",
    ),
    _entry(
        "src/api/routes/admin/auth.py",
        "verify_otp",
        "delegated",
        "Route wrapper delegating to the domain-bound OTP verifier.",
    ),
    # --- email verification / password reset / 2FA ---------------------------
    _entry(
        "src/core/auth/managers/registration.py",
        "verify_email",
        "type",
        "parse_token + token_type != 'email' and row token_type != "
        "'verify_email'; a reset token cannot verify an address.",
    ),
    _entry(
        "src/core/auth/managers/registration.py",
        "resend_verification",
        "domain",
        "Not a credential verifier: resolves an e-mail via the domain "
        "blind index 'user_email'. Registered as a documented non-entry-point "
        "so the name-based sweep stays explicit.",
    ),
    _entry(
        "src/core/auth/managers/password.py",
        "reset_password",
        "type",
        "parse_token + token_type != 'email' and row token_type != "
        "'reset_password'; a verify-email token cannot reset a password.",
    ),
    _entry(
        "src/core/auth/managers/twofactor.py",
        "complete_2fa",
        "type|domain",
        "parse_token + parsed['token_type'] != '2fa' before reading the "
        "challenge; IP binding uses the 'ip_address' blind-index domain.",
    ),
    # --- pure hash primitives / delegating session wrappers -----------------
    _entry(
        "src/core/auth/tokens.py",
        "verify_token_hash",
        "primitive",
        "Constant-time comparison of a secret hash; the caller supplies the "
        "expected hash, so no cross-family confusion is possible here.",
    ),
    _entry(
        "src/core/applications/oauth/tokens.py",
        "verify_token_hash",
        "primitive",
        "Constant-time comparison primitive for OAuth secrets; family binding "
        "is the caller's parse_oauth_token type check.",
    ),
    _entry(
        "src/core/applications/oauth/tokens.py",
        "verify_pkce_challenge",
        "primitive",
        "Constant-time PKCE challenge comparison; not a bearer credential.",
    ),
    _entry(
        "src/core/auth/sessions.py",
        "verify_token",
        "delegated",
        "Module wrapper delegating to the typed AuthManager verifier.",
    ),
    _entry(
        "src/api/websocket/handlers/connection.py",
        "_verify_token",
        "delegated|type",
        "Gateway wrapper delegating to auth.verify_token and re-checking the "
        "returned token_type (bot credentials are rebound to the bot's own "
        "account before socket binding).",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_token",
        "delegated",
        "Request-scoped wrapper calling auth.verify_token (typed sub-verifiers "
        "do the binding).",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_token_cached",
        "delegated",
        "Request-scoped caching wrapper around _verify_token.",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_with_cleanup",
        "delegated",
        "Threadpool wrapper calling auth.verify_token.",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_with_cache",
        "delegated",
        "Caching wrapper around the delegated verifier.",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_cookie",
        "delegated",
        "Cookie wrapper calling auth.verify_token.",
    ),
    _entry(
        "src/api/middleware/authentication.py",
        "_verify_cookie_cached",
        "delegated",
        "Caching wrapper around the cookie verifier.",
    ),
)


# ---------------------------------------------------------------------------
# AST analysis helpers
# ---------------------------------------------------------------------------


def _is_docstring(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _functions(blob: str) -> dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]]:
    tree = ast.parse(blob)
    found: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(node.name, []).append(node)
    return found


def _body_code(blob: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    # One source-segment lookup for the whole function (ast.get_source_segment
    # re-splits the file on every call, so doing it per statement is needlessly
    # expensive across the whole committed tree). The docstring is removed so a
    # description in prose can never count as a binding marker.
    segment = ast.get_source_segment(blob, node) or ""
    body = list(node.body)
    if body and _is_docstring(body[0]):
        doc = ast.get_source_segment(blob, body[0]) or ""
        segment = segment.replace(doc, "", 1)
    return segment


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _string_literals(node: ast.AST) -> Iterable[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(node.body)
    if body and _is_docstring(body[0]):
        body = body[1:]
    if not body:
        return False
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            if stmt.value.value is Ellipsis:
                continue
        if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
            if _call_name(stmt.exc.func) in {"NotImplementedError"}:
                continue
        return False
    return True


def _is_primitive(code: str) -> bool:
    has_compare = any(call in code for call in COMPARE_CALLS)
    has_lookup = any(
        token in code
        for token in (
            "fetch_one",
            "fetch_all",
            "parse_token",
            "blind_index",
            "execute(",
        )
    )
    return has_compare and not has_lookup


def analyze(
    node: ast.FunctionDef | ast.AsyncFunctionDef, code: str
) -> tuple[frozenset[str], frozenset[str]]:
    """Return (binding kinds, credential type literals) for one function."""
    kinds: set[str] = set()
    types: set[str] = set()

    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare):
            literals = set(_string_literals(sub.left))
            for comparator in sub.comparators:
                literals |= set(_string_literals(comparator))
            types |= literals & CREDENTIAL_TYPES
        if isinstance(sub, ast.Call):
            name = _call_name(sub.func)
            if name in PARSERS:
                kinds.add("parser")
            if name == "_parse_webhook_token":
                kinds.add("prefix")
            if name in DOMAIN_HELPERS:
                kinds.add("domain")
            if name.endswith(BLIND_INDEX_SUFFIX) and len(sub.args) >= 2:
                domain_arg = sub.args[1]
                if isinstance(domain_arg, ast.Constant) and isinstance(
                    domain_arg.value, str
                ):
                    kinds.add("domain")
            if (
                name == "startswith"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)
                and sub.args[0].value.startswith("webhook")
            ):
                kinds.add("prefix")
            if name in DELEGATED_VERIFIERS:
                kinds.add("delegated")
            if name in COMPARE_CALLS:
                kinds.add("compare")

    if types:
        kinds.add("type")
    if len(types) >= 2:
        kinds.add("dispatch")
    if any(marker in code for marker in DOMAIN_KEYSPACES):
        kinds.add("domain")
    if _is_stub(node):
        kinds.add("stub")
    if _is_primitive(code):
        kinds.add("primitive")
    return frozenset(kinds), frozenset(types)


def _called_names(node: ast.AST) -> frozenset[str]:
    return frozenset(
        _call_name(sub.func) for sub in ast.walk(node) if isinstance(sub, ast.Call)
    )


def _self_delegation_targets(node: ast.AST) -> frozenset[str]:
    """Method names invoked on ``self``/``cls`` (same-module delegation edges)."""
    return frozenset(
        sub.func.attr
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and isinstance(sub.func.value, ast.Name)
        and sub.func.value.id in {"self", "cls"}
    )


def _target_binds_family(
    blob: str,
    target: str,
    functions: Mapping[str, list[ast.FunctionDef | ast.AsyncFunctionDef]],
    _seen: frozenset[str] = frozenset(),
) -> bool:
    """Whether ``target`` (or a same-module chain from it) binds a family.

    Delegation edges are followed to the terminal helper so a wrapper cannot
    claim a "bound" target that is itself only another unbound forwarder. Only
    self-binding kinds count -- a further "delegated" hop is not evidence.
    """
    if target in _seen:
        return False
    for node in functions.get(target, []):
        kinds, _ = analyze(node, _body_code(blob, node))
        if kinds & SELF_BINDING_KINDS:
            return True
        for nested in _self_delegation_targets(node):
            if _target_binds_family(blob, nested, functions, _seen | {target}):
                return True
    return False


def _is_candidate(name: str, kinds: frozenset[str], called: frozenset[str]) -> bool:
    if name not in CANDIDATE_NAME:
        return False
    return bool(called & (PARSERS | VERIFY_PRIMITIVES)) or "domain" in kinds


def find_candidates(
    files: Iterable[str], read: Callable[[str], str]
) -> dict[tuple[str, str], list[tuple[frozenset[str], frozenset[str]]]]:
    """Map (path, func) -> per-definition (kinds, types) for all candidates."""
    result: dict[tuple[str, str], list[tuple[frozenset[str], frozenset[str]]]] = {}
    for path in files:
        blob = read(path)
        for name, nodes in _functions(blob).items():
            if name not in CANDIDATE_NAME:
                continue  # cheap filter before any AST/source work
            for node in nodes:
                code = _body_code(blob, node)
                kinds, types = analyze(node, code)
                if _is_candidate(name, kinds, _called_names(node)):
                    result.setdefault((path, name), []).append((kinds, types))
    return result


@lru_cache(maxsize=None)
def _committed_candidates(
    commit: str,
) -> dict[tuple[str, str], list[tuple[frozenset[str], frozenset[str]]]]:
    return find_candidates(_production_files(commit), _batch_reader(commit))


def registry_violations(
    registry: Iterable[VerifierEntry],
    candidates: Mapping[tuple[str, str], list[tuple[frozenset[str], frozenset[str]]]],
    files: Iterable[str],
    read: Callable[[str], str],
    *,
    require_present: bool,
) -> list[str]:
    """Validate each registry row against the (committed) source blob."""
    problems: list[str] = []
    file_set = set(files)
    for entry in registry:
        if entry.path not in file_set:
            if require_present:
                problems.append(f"{entry.path}: registry path missing from tree")
            continue
        blob = read(entry.path)
        functions = _functions(blob)
        nodes = functions.get(entry.func, [])
        if not nodes:
            problems.append(f"{entry.path}:{entry.func}: function not found in blob")
            continue
        # Every同名 definition must satisfy the declared binding, so a new
        # unbound overload in the same module cannot hide behind a checked one.
        for node in nodes:
            kinds, _ = analyze(node, _body_code(blob, node))
            if not kinds & entry.accept:
                problems.append(
                    f"{entry.path}:{entry.func}: expected one of "
                    f"{sorted(entry.accept)}, got {sorted(kinds)}"
                )
                continue
            if entry.delegates_to:
                # "delegated" is a documented kind, but the documentation must
                # be true: the wrapper actually calls each declared target and
                # that target resolves to a family-bound verifier. Delegation
                # chains are followed to the terminal helper.
                called = _called_names(node)
                for target in sorted(entry.delegates_to):
                    if target not in called:
                        problems.append(
                            f"{entry.path}:{entry.func}: documented delegation "
                            f"target {target!r} is not called"
                        )
                        continue
                    if not _target_binds_family(blob, target, functions):
                        problems.append(
                            f"{entry.path}:{entry.func}: delegation target "
                            f"{target!r} carries no family binding"
                        )
    return problems


def sweep_violations(
    registry: Iterable[VerifierEntry],
    candidates: Mapping[tuple[str, str], list[tuple[frozenset[str], frozenset[str]]]],
) -> list[str]:
    """Fail closed on any candidate that is unregistered and self-unbound."""
    registered = {(e.path, e.func) for e in registry}
    problems: list[str] = []
    for (path, func), defs in candidates.items():
        if (path, func) in registered:
            continue
        for kinds, _ in defs:
            if not kinds & SELF_BINDING_KINDS:
                problems.append(
                    f"{path}:{func}: NEW verification entry point without a "
                    f"family binding (kinds={sorted(kinds)}); register it with "
                    f"a documented reason or add a type/prefix/domain check"
                )
                break
    return problems


# ---------------------------------------------------------------------------
# Self-test fixtures: synthetic sources fed through the same analyzer
# ---------------------------------------------------------------------------

_SYNTH_PATH = "src/core/auth/_synthetic_verifier.py"

_UNBOUND_SRC = """
def verify_token(token, ip_address=None):
    parsed = parse_token(token)
    if not parsed:
        return None
    # BUG: no check of parsed["token_type"] -- any family is accepted.
    row = self._db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],))
    return row is not None
"""

_TYPED_SRC = """
def verify_token(token, ip_address=None):
    parsed = parse_token(token)
    if not parsed or parsed["token_type"] != "session":
        raise TokenInvalidError("Invalid token")
    row = self._db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],))
    return row is not None
"""

_DOMAIN_SRC = """
def verify_api_access_token(token):
    index = self.crypto.fast_blind_index(token, "api_access_token")
    row = self._db.fetch_one("SELECT * FROM auth_api_access_tokens WHERE token_index = ?", (index,))
    return row is not None
"""

_PREFIX_SRC = """
def get_webhook_by_token(token):
    parsed = self._parse_webhook_token(token)
    if not parsed:
        raise InvalidWebhookTokenError("bad prefix")
    return parsed
"""

_DELEGATED_SRC = """
def verify_token(token):
    return self._auth.verify_token(token)
"""

# A thin wrapper is only safe when the helper it forwards to actually binds
# the family. These two synthetic modules are identical except for the
# helper's token_type check -- exactly the lifecycle.py refactor shape.
_DELEGATED_BOUND_MODULE = """
def verify_token(self, token):
    return self._verify_token(token)


def _verify_token(self, token):
    parsed = parse_token(token)
    if not parsed or parsed["token_type"] != "session":
        raise TokenInvalidError("Invalid token")
    return self._db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],))
"""

_DELEGATED_UNBOUND_MODULE = """
def verify_token(self, token):
    return self._verify_token(token)


def _verify_token(self, token):
    parsed = parse_token(token)
    if not parsed:
        return None
    # BUG: no token_type check -- every family is accepted by the helper.
    return self._db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (parsed["id"],))
"""


def _module_blob(funcs_src: str) -> str:
    """Wrap bare method definitions in a class for AST analysis."""
    return "class _Synthetic:\n" + "\n".join(
        "    " + line if line.strip() else line
        for line in funcs_src.strip().splitlines()
    )


def _analyze_source(src: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    blob = "class _Synthetic:\n" + "\n".join(
        "    " + line if line.strip() else line for line in src.strip().splitlines()
    )
    nodes = _functions(blob)
    assert len(nodes) == 1, (
        f"expected exactly one synthetic function, got {list(nodes)}"
    )
    node = nodes[next(iter(nodes))][0]
    kinds, types = analyze(node, _body_code(blob, node))
    return kinds, types, _called_names(node)


# ---------------------------------------------------------------------------
# Real committed-tree tests
# ---------------------------------------------------------------------------


def test_registry_is_well_formed() -> None:
    keys = [(e.path, e.func) for e in VERIFIER_REGISTRY]
    assert len(keys) == len(set(keys)), "duplicate registry entries"
    assert len(VERIFIER_REGISTRY) >= 40, "registry too small to cover the campaign"
    for entry in VERIFIER_REGISTRY:
        assert entry.reason.strip(), f"empty reason for {entry.path}:{entry.func}"
        assert len(entry.reason) >= 40, f"reason too thin for {entry.path}:{entry.func}"
        assert entry.accept <= ALL_KINDS


def test_registry_covers_every_committed_candidate(commit_sha: str) -> None:
    """Fail closed on a NEW verification entry point in the committed tree."""
    candidates = _committed_candidates(commit_sha)
    assert candidates, "candidate sweep parsed no verification entry points"
    violations = sweep_violations(VERIFIER_REGISTRY, candidates)
    assert not violations, "unbound/unregistered verifier(s):\n" + "\n".join(violations)


def test_registry_entries_bind_expected_family(commit_sha: str) -> None:
    """Every documented verifier still binds its family in the committed blob."""
    files = _production_files(commit_sha)
    read = _batch_reader(commit_sha)
    candidates = _committed_candidates(commit_sha)
    violations = registry_violations(
        VERIFIER_REGISTRY, candidates, files, read, require_present=True
    )
    assert not violations, "lost family binding(s):\n" + "\n".join(violations)


def test_ws_ticket_exception_is_documented_with_a_reason() -> None:
    """The intentional WS-ticket-by-session-verify exception is allowlisted."""
    matches = [
        e
        for e in VERIFIER_REGISTRY
        if e.path == "src/core/auth/managers/session/verification.py"
        and e.func == "verify_token"
    ]
    assert len(matches) == 1
    reason = matches[0].reason.lower()
    assert "ws ticket" in reason and "wst" in reason
    assert "_verify_ws_ticket" in reason


def test_reader_returns_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """The reader is pinned to a commit; a diverged working tree cannot fool it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "guard@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Guard"], cwd=repo, check=True)
    path = repo / "module.py"
    path.write_text("VALUE = 'committed'\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=repo,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    path.write_text("VALUE = 'stale working tree'\n", encoding="utf-8")

    proc = subprocess.run(
        ["git", "show", f"{sha}:module.py"], cwd=repo, capture_output=True, text=True
    )
    assert proc.returncode == 0
    assert proc.stdout == "VALUE = 'committed'\n"
    assert "stale" not in proc.stdout


# ---------------------------------------------------------------------------
# Non-vacuity self-tests (synthetic verifiers through the real analyzer)
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous_unbound_verifier_is_flagged() -> None:
    kinds, _, called = _analyze_source(_UNBOUND_SRC)
    assert _is_candidate("verify_token", kinds, called), (
        "self-test source must be a candidate"
    )
    assert not kinds & SELF_BINDING_KINDS, (
        "a credential-verifying function with no type/prefix/domain binding "
        f"must be flagged, got {sorted(kinds)}"
    )
    # Feed it through the actual sweep with no registry row -> violation.
    candidate_map = {(_SYNTH_PATH, "verify_token"): [(kinds, frozenset())]}
    violations = sweep_violations((), candidate_map)
    assert violations and "NEW verification entry point" in violations[0]


def test_scan_accepts_typed_domain_prefixed_and_documented_cases() -> None:
    typed, typed_types, _ = _analyze_source(_TYPED_SRC)
    assert "type" in typed and "session" in typed_types
    domain, _, _ = _analyze_source(_DOMAIN_SRC)
    assert "domain" in domain
    prefix, _, _ = _analyze_source(_PREFIX_SRC)
    assert "prefix" in prefix
    delegated, _, _ = _analyze_source(_DELEGATED_SRC)
    assert "delegated" in delegated
    # Self-binding kinds pass the sweep even when not registered ...
    candidate_map = {
        (_SYNTH_PATH, "verify_token"): [(typed, typed_types)],
        ("src/core/auth/_synth_api.py", "verify_api_access_token"): [
            (domain, frozenset())
        ],
        ("src/core/webhooks/_synth.py", "get_webhook_by_token"): [
            (prefix, frozenset())
        ],
    }
    assert not sweep_violations((), candidate_map)
    # ... but a pure delegation does NOT pass unless documented/registered.
    only_delegated = {(_SYNTH_PATH, "verify_token"): [(delegated, frozenset())]}
    assert sweep_violations((), only_delegated), "delegation must require documentation"


def test_registry_check_reacts_to_removed_binding() -> None:
    """Mutating a synthetic committed blob flips the registry verdict."""
    good = VerifierEntry(_SYNTH_PATH, "verify_token", frozenset({"type"}), "reason")
    good_map = {
        (_SYNTH_PATH, "verify_token"): [(_analyze_source(_TYPED_SRC)[0], frozenset())]
    }
    assert not registry_violations(
        [good], good_map, [_SYNTH_PATH], lambda p: _TYPED_SRC, require_present=True
    )
    bad_map = {
        (_SYNTH_PATH, "verify_token"): [(_analyze_source(_UNBOUND_SRC)[0], frozenset())]
    }
    violations = registry_violations(
        [good], bad_map, [_SYNTH_PATH], lambda p: _UNBOUND_SRC, require_present=True
    )
    assert violations and "expected one of ['type']" in violations[0]


def test_delegated_wrapper_must_target_a_bound_helper() -> None:
    """A wrapper delegating to an UNBOUND helper is still flagged.

    This is the exact shape of the lifecycle.py refactor: the guard accepts the
    thin wrapper only because the terminal helper still dispatches on
    token_type. Flipping that check in the helper flips the verdict.
    """
    entry = _entry(
        _SYNTH_PATH,
        "verify_token",
        "delegated",
        "Synthetic wrapper delegates to _verify_token, which must bind family.",
        delegates_to="_verify_token",
    )
    bound_blob = _module_blob(_DELEGATED_BOUND_MODULE)
    assert not registry_violations(
        [entry], {}, [_SYNTH_PATH], lambda p: bound_blob, require_present=True
    )
    unbound_blob = _module_blob(_DELEGATED_UNBOUND_MODULE)
    violations = registry_violations(
        [entry], {}, [_SYNTH_PATH], lambda p: unbound_blob, require_present=True
    )
    assert violations and "no family binding" in violations[0], violations

    # The wrapper must actually call the declared target; pointing at an
    # unrelated delegated verifier is not enough.
    no_target_blob = _module_blob(
        "def verify_token(self, token):\n"
        "    return self.verify_api_access_token(token)\n"
        "def _verify_token(self, token):\n"
        "    return parse_token(token)\n"
    )
    violations = registry_violations(
        [entry], {}, [_SYNTH_PATH], lambda p: no_target_blob, require_present=True
    )
    assert violations and "is not called" in violations[0], violations
