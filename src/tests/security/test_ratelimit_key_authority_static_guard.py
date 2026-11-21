"""Structural guard: security-critical limiters stay authoritatively keyed.

A follow-up to the rate-limit key audit. Every security-critical limiter must
derive its bucket/identity from an *authoritative* source -- a validated
user/admin id, the trusted client IP (``get_client_ip`` / ``cached_client_ip``),
or a non-reversible token fingerprint (SHA-256) -- and never from a raw request
field, a forwarded-for header, or a client-supplied identifier. The central
limiter must also fail closed when its store is unavailable.

These invariants are easy to regress silently: a limiter keyed on a client value
lets the caller pick its own bucket (unlimited guesses), and a limiter that
fails open on a store outage is an unbounded free-fire window. Runtime tests
exercise each limiter individually, but nothing stops a future edit from
swapping a key expression for ``request.headers[...]`` or deleting a fail-closed
marker. This is a pure-AST ratchet (no app import, config or DB) with three
halves:

1. A documented allowlist of the audited key-construction sites (login, OTP,
   invite creation, 2FA, token verify, share-link guesses, password-reset
   throttle). Each entry is parsed from source and must still contain its
   authoritative key evidence and no client-supplied marker.

2. A whole-tree sweep of every ``check_rate_limit`` / ``acheck_rate_limit``
   call under ``src/`` (excluding tests/selftest). A call whose key expression
   uses a raw request field, a forwarded-for header or a client-supplied id is
   flagged regardless of the function name, so a NEW limiter call site cannot
   sneak in with a client-controlled key just because nobody added it to the
   allowlist. The classifier is deliberately narrow -- a bare ``request``
   passed to a trusted resolver (``cached_client_ip(request, ...)``) is fine;
   an attribute rooted at ``body``/``request``/``headers`` or a forwarded-for
   literal is not. On the current tree it flags nothing (verified below).

3. The central limiter's fail-closed marker (``create_store_unavailable_result``
   returning ``allowed=False``) must still be present and wired into the
   middleware/atomic paths.

Every detector is exercised against synthetic good/bad modules first, and a
non-vacuity test mutates real in-body evidence to prove the guard is not
blindly passing. Nothing is written to disk -- all mutations are in-memory.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.unit, pytest.mark.security]


# --- vocabulary ------------------------------------------------------------

# Names that mean "raw client input". A bare request object is *soft*: it is
# fine when handed to a trusted resolver, bad when a field is read off it.
CLIENT_ROOTS = frozenset(
    {"body", "payload", "form", "params", "request", "data", "query", "args"}
)
CLIENT_ATTRS = ("header", "query_param", "cookie", "forwarded", "query_string")
CLIENT_NAMES = ("client_supplied", "untrusted", "unvalidated", "raw_key", "raw_id")
CLIENT_STRINGS = (
    "x-forwarded-for",
    "x_forwarded_for",
    "x-real-ip",
    "x-real_ip",
    "cf-connecting-ip",
    "cf_connecting_ip",
    "x-client-ip",
    "x_client_ip",
)

# Names/attributes/calls that establish an authoritative key source.
AUTH_MARKERS = (
    "get_client_ip",
    "cached_client_ip",
    "client_ip",
    "trusted_ip",
    "user_id",
    "admin_id",
    "owner_id",
    "current_user",
    "sha256",
    "hashlib",
    "fingerprint",
    "principal",
    "blind_index",
    "email_index",
    "validate",
    "_key",
)


@dataclass(frozen=True)
class KeySite:
    """One documented, audited key-construction site.

    ``key_fragment`` is a literal substring that must remain in ``func``'s own
    body (defining it in a dead nested helper does not count). ``authoritative``
    lists markers at least one of which must remain on that evidence.
    """

    label: str
    rel_path: str
    func: str
    key_fragment: str
    authoritative: tuple[str, ...]
    why: str


# The seven audited categories (token verify has two call sites). Each entry
# is safe *because* of the documented authoritative source, not merely because
# it is listed here.
DOCUMENTED_KEY_SITES: tuple[KeySite, ...] = (
    KeySite(
        "admin login",
        "core/admin/auth/helpers.py",
        "_check_rate_limit",
        "admin_login:",
        ("ip",),
        "Bucket is the trusted client address supplied by authenticate_admin "
        "(get_client_ip at the route boundary), never a submitted username/id.",
    ),
    KeySite(
        "admin OTP account budget",
        "core/admin/auth/otp_mixin.py",
        "_check_admin_otp_budget",
        "admin_otp_account:",
        ("admin_id",),
        "Account-wide OTP guess budget keyed by the validated admin id, so "
        "freshly minted challenge tokens cannot reset the guessing budget.",
    ),
    KeySite(
        "invite creation",
        "core/servers/handlers/members/invites.py",
        "_enforce_invite_quota",
        "invites:creation:",
        ("user_id",),
        "Shared atomic creation budget keyed by the authenticated inviter id.",
    ),
    KeySite(
        "2FA attempts",
        "core/auth/managers/twofactor.py",
        "_check_and_record_2fa_attempt",
        "auth_2fa_attempts:",
        ("user_id",),
        "Shared per-user TOTP guessing budget keyed by the validated user id; "
        "fresh challenge tokens cannot reset it, and store errors fail closed.",
    ),
    KeySite(
        "token verify (verification path)",
        "core/auth/managers/session/verification.py",
        "verify_token",
        "token_verify:",
        ("ip_address",),
        "Verification throttle keyed by the trusted client IP, never by the "
        "raw bearer token or a request field.",
    ),
    KeySite(
        "token verify (lifecycle path)",
        "core/auth/managers/session/lifecycle.py",
        "verify_token",
        "token_verify:",
        ("ip_address",),
        "Same trusted-IP throttle on the second verify entry point.",
    ),
    KeySite(
        "share-link password guesses",
        "api/routes/suite/stash/share_links.py",
        "_share_link_verify_key",
        "stash:share-link-verify:",
        ("sha256",),
        "Bucket is the SHA-256 fingerprint of the share token, so the "
        "credential is neither written to the store nor client-selectable.",
    ),
    KeySite(
        "password reset throttle",
        "core/auth/managers/password.py",
        "request_password_reset",
        "reset_password",
        ("user_id",),
        "Per-address throttle is keyed by the user id resolved from the "
        "blinded email index, not by the attacker-supplied email string.",
    ),
)

# No current call site is allowed to be client-keyed. A future, reviewed
# exception would be recorded here with a concrete reason.
CLIENT_KEYED_ALLOWLIST: tuple[tuple[str, int], ...] = ()


# --- source / AST helpers --------------------------------------------------

@lru_cache(maxsize=None)
def _source(rel_path: str) -> str:
    return (SRC / rel_path).read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _tree(rel_path: str) -> ast.Module:
    return ast.parse(_source(rel_path))


def _find_function(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(node: ast.AST):
    """Nodes on ``node``'s own execution path, skipping nested callables."""
    stack = [node]
    while stack:
        for child in ast.iter_child_nodes(stack.pop()):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            stack.append(child)


def _root_name(node: ast.AST) -> str:
    """Root identifier of an attribute/subscript chain (``body.user_id`` -> body)."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _is_client_keyed(key_node: ast.AST) -> bool:
    """True when a key expression reads raw client input.

    HARD markers (an attribute rooted at a request/body object, a header-like
    attribute, or a forwarded-for literal) are always client-controlled. A bare
    request object is SOFT: it is acceptable only when the same expression also
    uses an authoritative resolver (e.g. ``cached_client_ip(request, ...)``).
    """
    hard = soft = auth = False
    for sub in ast.walk(key_node):
        if isinstance(sub, ast.Attribute):
            if _root_name(sub) in CLIENT_ROOTS:
                hard = True
            if any(m in sub.attr.lower() for m in CLIENT_ATTRS):
                hard = True
            if any(m in sub.attr.lower() for m in AUTH_MARKERS):
                auth = True
        elif isinstance(sub, ast.Name):
            low = sub.id.lower()
            if low in CLIENT_ROOTS:
                soft = True
            if any(m in low for m in CLIENT_NAMES):
                hard = True
            if any(m in low for m in AUTH_MARKERS):
                auth = True
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if any(m in sub.value.lower() for m in CLIENT_STRINGS):
                hard = True
    return hard or (soft and not auth)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _key_args(call: ast.Call) -> list[ast.AST]:
    """Key-bearing arguments of a limiter call."""
    out: list[ast.AST] = []
    if call.args:
        out.append(call.args[0])
    for kw in call.keywords:
        if kw.arg in ("key", "user_id", "ip_address", "resource_id", "ip"):
            out.append(kw.value)
    return out


def _production_sources():
    """(rel_path, source) for every production module under src/."""
    for path in sorted(SRC.rglob("*.py")):
        parts = path.parts
        if "tests" in parts or "selftest" in parts:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        yield path.relative_to(SRC).as_posix(), source


def _client_keyed_calls(sources):
    """Find (rel_path, line, key_source) limiter calls keyed from client input."""
    flagged = []
    for rel_path, source in sources:
        if "check_rate_limit" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) not in ("check_rate_limit", "acheck_rate_limit"):
                continue
            for key_node in _key_args(node):
                if _is_client_keyed(key_node):
                    flagged.append(
                        (rel_path, node.lineno, ast.get_source_segment(source, key_node) or "")
                    )
    return flagged


@lru_cache(maxsize=1)
def _real_client_keyed_calls():
    return tuple(_client_keyed_calls(tuple(_production_sources())))


@lru_cache(maxsize=1)
def _swept_call_count():
    total = 0
    for _rel, source in _production_sources():
        if "check_rate_limit" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and _call_name(node) in (
                "check_rate_limit",
                "acheck_rate_limit",
            ):
                total += 1
    return total


# --- half 1: documented key sites ------------------------------------------

def test_documented_key_sites_are_real_and_authoritative():
    """Every audited site still builds its key from the recorded authoritative source."""
    for site in DOCUMENTED_KEY_SITES:
        fn = _find_function(_tree(site.rel_path), site.func)
        assert fn is not None, f"{site.rel_path}: function {site.func!r} not found"
        body_source = ast.get_source_segment(_source(site.rel_path), fn) or ""
        assert site.key_fragment in body_source, (
            f"{site.label}: key fragment {site.key_fragment!r} left "
            f"{site.rel_path}:{site.func}"
        )
        evidence = [
            node
            for node in _own_body_nodes(fn)
            if isinstance(node, (ast.JoinedStr, ast.Constant, ast.Call, ast.Attribute))
            and site.key_fragment
            in (ast.get_source_segment(_source(site.rel_path), node) or "")
        ]
        assert evidence, (
            f"{site.label}: key fragment {site.key_fragment!r} only appears in "
            "dead nested code, not in the function's own body"
        )
        # Authoritative evidence may live in a sibling statement of the key
        # fragment (e.g. sha256() produces ``fingerprint`` used by the f-string),
        # so check markers across the function's own executable body.
        own_source = "\n".join(
            ast.get_source_segment(_source(site.rel_path), node) or ""
            for node in _own_body_nodes(fn)
            if isinstance(node, ast.stmt)
        )
        assert any(m in own_source for m in site.authoritative), (
            f"{site.label}: no authoritative marker {site.authoritative} remains "
            f"in {site.rel_path}:{site.func}"
        )
        for node in evidence:
            assert not _is_client_keyed(node), (
                f"{site.label}: key evidence now reads client input: "
                f"{ast.get_source_segment(_source(site.rel_path), node)!r}"
            )


def test_password_reset_throttle_uses_resolved_user_id():
    """The reset throttle is keyed by a blinded-email-resolved id, not the email."""
    fn = _find_function(
        _tree("core/auth/managers/password.py"), "request_password_reset"
    )
    assert fn is not None
    src = ast.get_source_segment(_source("core/auth/managers/password.py"), fn) or ""
    assert "blind_index" in src and "email_index" in src, (
        "password reset no longer resolves the submitted email to an authoritative id"
    )
    assert "user_id = ?" in src and (
        'user["id"]' in src or "user['id']" in src
    ), "reset throttle lost its user-id key"
    assert not _name_in_key_string(fn, "email"), (
        "reset throttle now builds a key from the raw, client-supplied email"
    )


def _name_in_key_string(fn: ast.AST, name: str) -> bool:
    """True when ``name`` is interpolated into an f-string inside ``fn``."""
    for node in _own_body_nodes(fn):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue) and isinstance(
                    value.value, ast.Name
                ):
                    if value.value.id == name:
                        return True
    return False


def test_documented_sites_have_reasons_and_distinct_locators():
    seen = set()
    for site in DOCUMENTED_KEY_SITES:
        assert (SRC / site.rel_path).is_file(), site.rel_path
        assert site.func and site.key_fragment and site.authoritative
        assert len(site.why.strip()) > 40, site.why
        key = (site.rel_path, site.func, site.key_fragment)
        assert key not in seen, f"duplicate documented key site: {key}"
        seen.add(key)
    assert len({s.label for s in DOCUMENTED_KEY_SITES}) == len(DOCUMENTED_KEY_SITES)


# --- half 2: whole-tree client-keyed sweep ---------------------------------

def test_no_client_keyed_limiter_calls():
    """A limiter keyed from a request field/header/client id must not exist."""
    flagged = _real_client_keyed_calls()
    allowed = set(CLIENT_KEYED_ALLOWLIST)
    unexpected = [(p, ln, k) for p, ln, k in flagged if (p, ln) not in allowed]
    assert not unexpected, (
        "security-critical limiter(s) keyed from client input:\n  "
        + "\n  ".join(f"{p}:{ln} -> {k}" for p, ln, k in unexpected)
        + "\nKey the bucket on a validated user/admin id, the trusted client IP, "
        "or a token fingerprint -- or record a reviewed verdict in the allowlist."
    )
    stale = sorted(a for a in allowed if a not in {(p, ln) for p, ln, _ in flagged})
    assert not stale, f"stale client-key allowlist entries: {stale}"


def test_whole_tree_sweep_is_not_vacuous():
    """The sweep must actually parse a meaningful number of limiter calls."""
    assert _swept_call_count() >= 20, (
        f"whole-tree sweep saw only {_swept_call_count()} limiter calls; the "
        "detector or its path filter regressed"
    )


def test_detector_flags_novel_client_keyed_call_site():
    """Non-vacuity: a brand-new limiter call site with a client key is caught."""
    novel = (
        "async def new_endpoint(request, body):\n"
        "    allowed, _ = check_rate_limit(\n"
        "        f\"new_op:{request.headers.get('X-Forwarded-For')}:{body.user_id}\",\n"
        "        5, 60,\n"
        "    )\n"
    )
    found = _client_keyed_calls([("api/routes/new_route.py", novel)])
    assert found, "synthetic client-keyed limiter call site was NOT flagged"
    assert found[0][0] == "api/routes/new_route.py"


def test_classifier_distinguishes_authoritative_from_client_keys():
    """Self-test: trusted keys accepted, raw request fields rejected."""
    good = [
        "allowed, _ = check_rate_limit(f\"login:{get_client_ip(request)}\", 5, 60)",
        "allowed, _ = check_rate_limit(f\"invites:creation:{user_id}\", 20, 60)",
        "allowed, _ = check_rate_limit(_share_link_verify_key(token), 10, 60)",
        "result = await ratelimit.acheck_rate_limit(\n"
        "    ip_address=cached_client_ip(request, resolver=get_client_ip)\n"
        ")",
    ]
    bad = [
        "allowed, _ = check_rate_limit(f\"login:{request.headers['X-Forwarded-For']}\", 5, 60)",
        "allowed, _ = check_rate_limit(f\"op:{body.user_id}\", 5, 60)",
        "allowed, _ = check_rate_limit(f\"op:{client_supplied_id}\", 5, 60)",
        "allowed, _ = check_rate_limit(f\"op:{request.query_params['id']}\", 5, 60)",
    ]
    for code in good:
        tree = ast.parse(code)
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert not any(_is_client_keyed(k) for k in _key_args(call)), code
    for code in bad:
        tree = ast.parse(code)
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        assert any(_is_client_keyed(k) for k in _key_args(call)), code


def test_detector_flags_in_memory_mutation_of_real_key():
    """Non-vacuity: swapping the real token-verify key for a raw request value flags it.

    The snippet mirrors the audited ``token_verify:{ip_address}`` key. With the
    trusted IP it is accepted; with a bare ``request`` (no authoritative
    resolver) the same call is reported as client-keyed.
    """
    good_src = (
        "def verify_token(self, token, ip_address=None):\n"
        "    allowed, _ = check_rate_limit(\n"
        "        key=f'token_verify:{ip_address}', limit=5, window_seconds=60\n"
        "    )\n"
    )
    bad_src = good_src.replace("ip_address", "request")
    assert not _client_keyed_calls([("probe.py", good_src)])
    flagged = _client_keyed_calls([("probe.py", bad_src)])
    assert flagged and "token_verify:{request}" in flagged[0][2], (
        "mutating the real token-verify key to a raw request value was NOT flagged"
    )


# --- half 3: central limiter fail-closed marker ----------------------------

def test_central_limiter_fails_closed_on_store_outage():
    """The store-outage result builder must exist and deny; it must be wired in."""
    results_src = _source("core/ratelimit/manager/results.py")
    results_tree = ast.parse(results_src)
    fn = _find_function(results_tree, "create_store_unavailable_result")
    assert fn is not None, "create_store_unavailable_result was removed"
    body = ast.get_source_segment(results_src, fn) or ""
    assert "allowed=False" in body.replace(" ", ""), (
        "store-outage result no longer denies (allowed=False missing)"
    )
    # The fail-closed builder must actually be used on the outage paths.
    for rel in ("core/ratelimit/middleware.py", "core/ratelimit/manager/atomic.py"):
        tree = _tree(rel)
        uses = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _call_name(n) == "create_store_unavailable_result"
        ]
        assert uses, f"{rel} no longer calls create_store_unavailable_result"


def test_fail_closed_marker_self_test():
    """Self-test: the marker detector rejects a fail-open result builder."""
    good = (
        "def create_store_unavailable_result(config, now):\n"
        "    return RateLimitResult(allowed=False)\n"
    )
    bad = (
        "def create_store_unavailable_result(config, now):\n"
        "    return RateLimitResult(allowed=True)\n"
    )
    for code, expected in ((good, True), (bad, False)):
        fn = _find_function(ast.parse(code), "create_store_unavailable_result")
        body = ast.get_source_segment(code, fn) or ""
        assert ("allowed=False" in body.replace(" ", "")) is expected
