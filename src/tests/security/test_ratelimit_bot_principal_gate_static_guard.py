"""Structural guard: manual route limiters reject bot tokens *before* charging the owner.

A bot token's ``user_id`` is its human owner (the bot id lives in
``account_id``). Any manual limiter keyed on ``current_user.user_id`` therefore
spends the *owner's* quota and lets a leaked bot evade its own. The campaign
that closed these holes added ``require_user_token(current_user)`` immediately
before each such spend (reactions, stickers, feedback, media, reports, search,
discovery, friend requests, ...). This guard makes that ordering a ratchet.

It is a pure-AST, whole-tree check (no app import/config/DB) with four halves:

1. Enumerate every manual limiter call under ``src/api/routes/**`` -- the known
   ``acheck_rate_limit`` / ``acheck`` / ``check_rate_limit`` /
   ``aincrement_custom_usage`` names plus any ``acheck*`` / ``*rate_limit`` /
   ``*custom_usage`` variant -- that actually carries a principal argument.
   Decorator-style/config calls (``@rate_limit(...)``, ``RateLimitConfig(...)``,
   ``is_rate_limited(exc)``) carry no principal and are ignored.

2. Classify the principal by provenance, not by a name allowlist:
   ``current_user.user_id`` (or a ``TokenInfo`` alias / a local copied from one)
   is CU-direct; a helper parameter fed by ``current_user.user_id`` at its call
   sites is CU-param; a SHA-256 token fingerprint or trusted client IP is an
   authoritative non-owner key. ``current_user.user_id`` is deliberately
   distinguished from other ids (``reported_user_id``, ``admin_id``, a raw
   request field, an interaction token).

3. Require a ``require_user_token(<same token>)`` statement to appear *before*
   the limiter spend in the same function (line/statement order, not just
   presence). For a helper keyed by a parameter, every call site that feeds it
   a CU-derived principal must have the gate before the helper call. A gate
   *after* the spend is a finding. Non-owner principals are accepted only when
   structurally authoritative AND documented in ``DOCUMENTED_AUTHORITATIVE``.

4. Non-vacuity: synthetic ungated / gated / gate-after / authoritative cases
   all flow through the same auditor, real sources are mutated in memory to
   prove a removed or reordered gate is caught, and the whole-tree sweep is
   asserted to parse a meaningful number of calls/sites.

Nothing is written to disk; every mutation is in-memory.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]
ROUTES = SRC / "api" / "routes"

pytestmark = [pytest.mark.unit, pytest.mark.security]

# Manual limiter entry points. ``aincrement_custom_usage`` is the atomic
# spend used by the fail-closed helpers; the rest are check-style spends.
LIMITER_EXACT = frozenset(
    {
        "acheck_rate_limit",
        "acheck",
        "check_rate_limit",
        "aincrement_custom_usage",
        "increment_custom_usage",
    }
)
# Calls for which positional argument 0 is the key/principal.
POSITIONAL_PRINCIPAL = frozenset(
    {
        "acheck_rate_limit",
        "acheck",
        "check_rate_limit",
        "aincrement_custom_usage",
        "increment_custom_usage",
    }
)
PRINCIPAL_KWARGS = frozenset(
    {
        "user_id",
        "principal",
        "owner_id",
        "actor_id",
        "subject_id",
        "key",
        "ip_address",
        "ip",
        "resource_id",
    }
)
GATE_NAME = "require_user_token"

# The only documented non-owner (authoritative-key) manual limiters in routes.
# A new authoritative-keyed site is structurally safe but must be recorded here
# with a reason before the documentation test passes; a new *user-id-keyed*
# site gets no such escape hatch -- it must carry the gate or it is a finding.
DOCUMENTED_AUTHORITATIVE: dict[tuple[str, str], tuple[str, str]] = {
    (
        "api/routes/applications.py",
        "respond_to_interaction",
    ): (
        "interaction token fingerprint",
        "The interaction callback is authenticated by a short-lived bearer "
        "token, not a user session. The bucket is the SHA-256 fingerprint "
        "built by _interaction_rate_limit_key, so the raw token is never "
        "stored and the caller cannot choose its own bucket; there is no "
        "current_user.user_id principal to gate on this path.",
    ),
    (
        "api/routes/suite/stash/share_links.py",
        "_enforce_share_link_verify_limit",
    ): (
        "share-link token fingerprint",
        "Share-link verification is authorized by the link token itself. "
        "_share_link_verify_key returns a SHA-256 fingerprint, so guesses are "
        "throttled per credential without persisting the bearer token and "
        "without descending to a user-owned bucket.",
    ),
    (
        "api/routes/telemetry.py",
        "submit_response_times",
    ): (
        "trusted client IP",
        "Telemetry is intentionally optional-auth (get_optional_user); the "
        "bucket is the trusted get_client_ip value, never a token's owner id, "
        "so there is no current_user.user_id spend to guard here.",
    ),
}


@dataclass(frozen=True)
class CallRecord:
    rel: str
    func: str
    line: int
    limiter: str
    principal: str
    kind: str  # cu_direct | cu_param | authoritative | other
    gated: bool


@dataclass(frozen=True)
class AuditResult:
    findings: tuple[str, ...]
    calls: tuple[CallRecord, ...]

    @property
    def cu_direct(self):
        return tuple(c for c in self.calls if c.kind == "cu_direct")

    @property
    def cu_param(self):
        return tuple(c for c in self.calls if c.kind == "cu_param")

    @property
    def authoritative(self):
        return tuple(c for c in self.calls if c.kind == "authoritative")


# --- source / AST vocabulary -----------------------------------------------

@lru_cache(maxsize=1)
def _route_sources() -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for path in sorted(ROUTES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append(
            (path.relative_to(SRC).as_posix(), path.read_text(encoding="utf-8"))
        )
    return tuple(out)


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_limiter_name(name: str) -> bool:
    if not name:
        return False
    if name in LIMITER_EXACT:
        return True
    low = name.lower()
    return low.startswith("acheck") or "rate_limit" in low or "custom_usage" in low


def _functions(tree: ast.AST):
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _own_nodes(fn: ast.AST, types):
    """Nodes on ``fn``'s own execution path, skipping nested callables."""
    stack = [fn]
    while stack:
        for child in ast.iter_child_nodes(stack.pop()):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, types):
                yield child
            stack.append(child)


def _own_calls(fn: ast.AST):
    return _own_nodes(fn, ast.Call)


def _own_stmts(fn: ast.AST):
    return _own_nodes(fn, ast.stmt)


def _expr_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _root_name(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def _all_args(fn: ast.AST) -> list[ast.arg]:
    a = fn.args
    return (
        list(a.posonlyargs)
        + list(a.args)
        + list(a.kwonlyargs)
        + ([a.vararg] if a.vararg else [])
        + ([a.kwarg] if a.kwarg else [])
    )


def _token_params(fn: ast.AST) -> set[str]:
    """Parameters that carry the authenticated user's TokenInfo."""
    signature = ast.unparse(fn.args)
    has_dep = "get_current_user" in signature or "get_optional_user" in signature
    out: set[str] = set()
    for arg in _all_args(fn):
        annotation = ast.unparse(arg.annotation) if arg.annotation else ""
        if "TokenInfo" in annotation or arg.arg == "current_user":
            out.add(arg.arg)
        elif has_dep and arg.arg in {"user", "token", "principal_token"}:
            out.add(arg.arg)
    return out


def _param_names(fn: ast.AST) -> set[str]:
    return {a.arg for a in _all_args(fn)}


def _principal_arg(call: ast.Call, name: str):
    """Return (principal_expr, keyword_or_None) for a manual limiter call."""
    for kw in call.keywords:
        if kw.arg in PRINCIPAL_KWARGS:
            return kw.value, kw.arg
    if name in POSITIONAL_PRINCIPAL and call.args:
        return call.args[0], None
    return None


def _local_derivations(
    fn: ast.AST, before_line: int, token_params: set[str], params: set[str]
):
    """Map locals written before ``before_line`` to the token/param they carry.

    Handles the two real shapes: ``user_id = current_user.user_id`` (token
    copy, e.g. feedback) and ``key = f"...{user_id}..."`` (helper parameter,
    e.g. stickers/friend_requests). Iterate to a fixpoint for chains of copies.
    """
    tok_roots: dict[str, set[str]] = {}
    param_roots: dict[str, set[str]] = {}
    changed = True
    while changed:
        changed = False
        for stmt in _own_stmts(fn):
            if stmt.lineno >= before_line:
                continue
            if isinstance(stmt, ast.Assign):
                targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
                value = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                targets = [stmt.target]
                value = stmt.value
            elif isinstance(stmt, ast.NamedExpr) and isinstance(stmt.target, ast.Name):
                targets = [stmt.target]
                value = stmt.value
            else:
                continue
            if value is None:
                continue
            names = _expr_names(value)
            for target in targets:
                name = target.id
                t = names & token_params
                for carried in names & tok_roots.keys():
                    t |= tok_roots[carried]
                p = names & params
                for carried in names & param_roots.keys():
                    p |= param_roots[carried]
                if t and not t <= tok_roots.get(name, set()):
                    tok_roots.setdefault(name, set()).update(t)
                    changed = True
                if p and not p <= param_roots.get(name, set()):
                    param_roots.setdefault(name, set()).update(p)
                    changed = True
    return tok_roots, param_roots


def _classify(expr: ast.AST, fn: ast.AST, before_line: int):
    """Return (kind, roots) for a principal expression inside ``fn``."""
    names = _expr_names(expr)
    tp = _token_params(fn)
    params = _param_names(fn)
    tok_roots, param_roots = _local_derivations(fn, before_line, tp, params)

    tokens = names & tp
    for carried in names & tok_roots.keys():
        tokens |= tok_roots[carried]
    if tokens:
        return "cu_direct", tokens

    ps = names & params
    for carried in names & param_roots.keys():
        ps |= param_roots[carried]
    if ps:
        return "cu_param", ps

    return "other", set()


def _gate_before(fn: ast.AST, call: ast.Call, tokens: set[str]) -> bool:
    """True iff require_user_token(<token>) is in ``fn`` before ``call``."""
    for gate in _own_calls(fn):
        if _call_name(gate.func) != GATE_NAME:
            continue
        if gate.lineno >= call.lineno:  # gate AFTER the spend does not count
            continue
        args = list(gate.args) + [kw.value for kw in gate.keywords]
        if not args:
            continue
        if _expr_names(args[0]) & tokens or _root_name(args[0]) in tokens:
            return True
    return False


def _is_authoritative(
    expr: ast.AST,
    kwarg: str | None,
    fn: ast.AST | None,
    before_line: int,
    helper_sources: dict[str, str],
) -> bool:
    """True for a non-owner key that is structurally authoritative.

    The fingerprint/IP can be built a statement earlier (``key = sha256(...)``)
    and only the resulting name passed to the limiter, so also inspect the
    local assignments feeding ``expr``'s names.
    """
    if kwarg in {"ip_address", "ip", "client_ip"}:
        text = ast.unparse(expr).lower()
        if any(
            marker in text
            for marker in ("client_ip", "get_client_ip", "cached_client_ip", "trusted_ip")
        ):
            return True
    if _expr_is_authoritative(expr, helper_sources):
        return True
    if fn is not None:
        names = _expr_names(expr)
        for stmt in _own_stmts(fn):
            if stmt.lineno >= before_line or not isinstance(stmt, ast.Assign):
                continue
            if stmt.value is None:
                continue
            if any(isinstance(t, ast.Name) and t.id in names for t in stmt.targets):
                if _expr_is_authoritative(stmt.value, helper_sources):
                    return True
    return False


def _expr_is_authoritative(expr: ast.AST, helper_sources: dict[str, str]) -> bool:
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Call):
            name = _call_name(sub.func).lower()
            src = helper_sources.get(name, "")
            if any(
                m in name or m in src
                for m in ("sha256", "fingerprint", "blind_index", "digest")
            ):
                return True
        elif isinstance(sub, ast.Name):
            if any(m in sub.id.lower() for m in ("sha256", "fingerprint")):
                return True
    return False


def _arg_for_param(ccall: ast.Call, fn: ast.AST, param: str):
    names = [a.arg for a in _all_args(fn)]
    if param not in names:
        return None
    idx = names.index(param)
    if idx < len(ccall.args):
        return ccall.args[idx]
    for kw in ccall.keywords:
        if kw.arg == param:
            return kw.value
    return None


# --- the auditor ------------------------------------------------------------

def _audit(sources) -> AuditResult:
    sources = tuple(sources)
    parsed: dict[str, tuple[str, ast.Module]] = {}
    for rel, source in sources:
        try:
            parsed[rel] = (source, ast.parse(source))
        except SyntaxError:
            continue

    funcs: list[tuple[str, ast.AST, str]] = []
    funcs_by_name: dict[str, list[tuple[str, ast.AST, str]]] = {}
    helper_sources: dict[str, str] = {}
    callsites: dict[str, list[tuple[str, ast.AST, ast.Call, str]]] = {}
    for rel, (source, tree) in parsed.items():
        for fn in _functions(tree):
            funcs.append((rel, fn, source))
            funcs_by_name.setdefault(fn.name, []).append((rel, fn, source))
            helper_sources[fn.name] = helper_sources.get(fn.name, "") + "\n" + (
                ast.get_source_segment(source, fn) or ""
            )
            for call in _own_calls(fn):
                name = _call_name(call.func)
                if name:
                    callsites.setdefault(name, []).append((rel, fn, call, source))

    findings: list[str] = []
    records: list[CallRecord] = []

    for rel, fn, source in funcs:
        for call in _own_calls(fn):
            name = _call_name(call.func)
            if not _is_limiter_name(name):
                continue
            principal = _principal_arg(call, name)
            if principal is None:
                continue
            expr, kwarg = principal
            text = ast.get_source_segment(source, expr) or ast.unparse(expr)
            where = f"{rel}:{call.lineno} ({fn.name})"

            if _is_authoritative(expr, kwarg, fn, call.lineno, helper_sources):
                records.append(CallRecord(rel, fn.name, call.lineno, name, text, "authoritative", True))
                continue

            kind, roots = _classify(expr, fn, call.lineno)

            if kind == "cu_direct":
                gated = _gate_before(fn, call, roots)
                records.append(
                    CallRecord(rel, fn.name, call.lineno, name, text, kind, gated)
                )
                if not gated:
                    findings.append(
                        f"{where}: limiter {name} spends a current-user-id "
                        f"bucket {text!r} without require_user_token() BEFORE it"
                    )

            elif kind == "cu_param":
                gated = True
                verified = False
                params = {a.arg for a in _all_args(fn)}
                for param in sorted(roots & params):
                    for crel, caller, ccall, csrc in callsites.get(fn.name, []):
                        arg = _arg_for_param(ccall, fn, param)
                        if arg is None:
                            continue
                        ckind, ctokens = _classify(arg, caller, ccall.lineno)
                        if ckind != "cu_direct":
                            continue
                        verified = True
                        if not _gate_before(caller, ccall, ctokens):
                            gated = False
                            findings.append(
                                f"{crel}:{ccall.lineno} ({caller.name}): call to "
                                f"{fn.name} feeds a current-user-id principal "
                                f"{ast.get_source_segment(csrc, arg) or ast.unparse(arg)!r} "
                                f"without require_user_token() BEFORE it "
                                f"(needed by {where})"
                            )
                records.append(
                    CallRecord(rel, fn.name, call.lineno, name, text, kind, gated)
                )
                if not verified:
                    findings.append(
                        f"{where}: limiter {name} is keyed by helper parameter "
                        f"{sorted(roots)} but no route call site feeds it a "
                        "gated current-user-id principal (failing closed)"
                    )

            else:
                records.append(
                    CallRecord(rel, fn.name, call.lineno, name, text, "other", False)
                )
                findings.append(
                    f"{where}: limiter {name} has principal {text!r} from an "
                    "unknown source; document it as an authoritative non-owner "
                    "key or gate the current-user-id spend"
                )

    return AuditResult(tuple(findings), tuple(records))


@lru_cache(maxsize=1)
def _real_audit() -> AuditResult:
    return _audit(_route_sources())


# --- half 1: the real tree --------------------------------------------------

def test_no_ungated_user_id_keyed_manual_limiter_in_routes():
    findings = _real_audit().findings
    assert not findings, "ungated/unknown manual limiters in routes:\n  " + "\n  ".join(
        findings
    )


def test_whole_tree_sweep_is_not_vacuous():
    audit = _real_audit()
    assert len(audit.calls) >= 15, (
        f"sweep saw only {len(audit.calls)} manual limiter calls; detector/path "
        "filter regressed"
    )
    assert len(audit.cu_direct) >= 5, (
        f"only {len(audit.cu_direct)} direct current-user-id limiters found"
    )
    assert len(audit.cu_param) >= 5, (
        f"only {len(audit.cu_param)} helper-parameter limiters found"
    )
    assert all(c.gated for c in audit.cu_direct + audit.cu_param), (
        "non-vacuity check: every CU-keyed call is expected to be gated on the "
        "audited tree"
    )


# --- half 2: documented authoritative keys ---------------------------------

def test_authoritative_sites_are_documented_and_still_authoritative():
    real = _real_audit()
    sites = {
        (c.rel, c.func)
        for c in real.authoritative
    }
    assert sites == set(DOCUMENTED_AUTHORITATIVE), (
        "authoritative-key limiter sites changed; update DOCUMENTED_AUTHORITATIVE "
        f"with reasons (found {sorted(sites)}, documented "
        f"{sorted(DOCUMENTED_AUTHORITATIVE)})"
    )
    for (rel, func), (marker, reason) in DOCUMENTED_AUTHORITATIVE.items():
        assert len(reason.strip()) > 40, f"{rel}:{func}: reason too thin"
        assert marker, f"{rel}:{func}: missing marker"


# --- half 3: synthetic self-tests (all through the same auditor) ------------

_TOKEN_ARG = "current_user: TokenInfo = Depends(get_current_user)"

_GATED = f"""
async def handler(request: Request, {_TOKEN_ARG}):
    require_user_token(current_user)
    rl = await ratelimit.acheck_rate_limit(
        user_id=current_user.user_id, route="X", is_internal=False
    )
"""

_UNGATED = _GATED.replace("    require_user_token(current_user)\n", "")

_GATE_AFTER = _UNGATED + "    require_user_token(current_user)\n"

_AUTHORITATIVE_TOKEN = """
async def callback(interaction_token: str):
    key = hashlib.sha256(interaction_token.encode()).hexdigest()
    allowed, _ = check_rate_limit(f"interaction:{key}", 5, 60)
"""

_AUTHORITATIVE_IP = f"""
async def telemetry(request: Request, {_TOKEN_ARG}):
    client_ip = get_client_ip(request)
    rl = await ratelimit.acheck_rate_limit(ip_address=client_ip)
"""

_NOVEL_HELPER_UNGATED = f"""
async def _check_brand_new_feature_quota(user_id: int) -> None:
    key = f"brand_new:minute:{{user_id}}"
    allowed, _ = check_rate_limit(key, 5, 60)


async def handler(body: Body, {_TOKEN_ARG}):
    await _check_brand_new_feature_quota(current_user.user_id)
"""

_NOVEL_HELPER_GATED = _NOVEL_HELPER_UNGATED.replace(
    "    await _check_brand_new_feature_quota(current_user.user_id)\n",
    "    require_user_token(current_user)\n"
    "    await _check_brand_new_feature_quota(current_user.user_id)\n",
)


def test_detector_flags_synthetic_ungated_user_id_limiter():
    audit = _audit([("api/routes/probe.py", _UNGATED)])
    assert audit.findings, "synthetic ungated current-user-id limiter was NOT flagged"
    assert "require_user_token" in audit.findings[0]


def test_detector_accepts_synthetic_gated_user_id_limiter():
    audit = _audit([("api/routes/probe.py", _GATED)])
    assert not audit.findings
    assert len(audit.cu_direct) == 1 and audit.cu_direct[0].gated


def test_detector_flags_gate_after_limiter():
    audit = _audit([("api/routes/probe.py", _GATE_AFTER)])
    assert audit.findings, "gate placed AFTER the limiter was NOT flagged"
    assert not all(c.gated for c in audit.cu_direct)


def test_detector_accepts_synthetic_authoritative_keys():
    for src in (_AUTHORITATIVE_TOKEN, _AUTHORITATIVE_IP):
        audit = _audit([("api/routes/probe.py", src)])
        assert not audit.findings, src
        assert len(audit.authoritative) == 1


def test_detector_flags_novel_helper_name_without_gate():
    ungated = _audit([("api/routes/probe.py", _NOVEL_HELPER_UNGATED)])
    gated = _audit([("api/routes/probe.py", _NOVEL_HELPER_GATED)])
    assert ungated.findings, "novel ungated helper limiter was NOT flagged"
    assert len(ungated.cu_param) == 1
    assert not gated.findings, "gating the novel helper call should clear the finding"


def test_classifier_distinguishes_current_user_from_other_principals():
    src = f"""
async def handler(reported_user_id: int, admin_id: int, {_TOKEN_ARG}):
    check_rate_limit(f"report:{{reported_user_id}}", 5, 60)
    check_rate_limit(f"admin:{{admin_id}}", 5, 60)
    check_rate_limit(f"owner:{{current_user.user_id}}", 5, 60)
"""
    fn = _functions(ast.parse(src))[0]
    other = _classify(ast.parse("f'report:{reported_user_id}'").body[0].value, fn, 1)
    cu = _classify(ast.parse("f'owner:{current_user.user_id}'").body[0].value, fn, 1)
    assert other[0] != "cu_direct", "non-owner id misclassified as a bot-token owner"
    assert "current_user" not in other[1]
    assert cu[0] == "cu_direct" and "current_user" in cu[1]


# --- half 4: in-memory mutations of the real tree ---------------------------

def test_removing_a_real_gate_is_detected():
    rel = "api/routes/reactions.py"
    source = dict(_route_sources())[rel]
    assert not _audit([(rel, source)]).findings
    mutated = source.replace(
        "    require_user_token(current_user)\n    # Rate limit reactions",
        "    # gate removed in-memory\n    # Rate limit reactions",
        1,
    )
    assert mutated != source
    findings = _audit([(rel, mutated)]).findings
    assert any("add_reaction" in f for f in findings), (
        "removing the real add_reaction gate was NOT detected: " + repr(findings)
    )


def test_moving_a_real_gate_after_the_spend_is_detected():
    rel = "api/routes/reactions.py"
    source = dict(_route_sources())[rel]
    gate = "    require_user_token(current_user)\n"
    spend = (
        '    rl_result = await ratelimit.acheck_rate_limit(\n'
        '        user_id=current_user.user_id,\n'
        '        route="PUT /reactions",\n'
        '        is_internal=getattr(request.state, "is_internal", False),\n'
        '    )\n'
    )
    assert gate + "    # Rate limit reactions" in source
    assert spend in source
    # Remove the leading gate, then re-insert it after the whole limiter call
    # so it is a later *statement*, not a line before the call.
    mutated = source.replace(gate, "", 1).replace(spend, spend + gate, 1)
    assert mutated != source
    findings = _audit([(rel, mutated)]).findings
    assert any("add_reaction" in f for f in findings), (
        "moving the real gate after the limiter was NOT detected: " + repr(findings)
    )


def test_removing_a_real_helper_call_site_gate_is_detected():
    rel = "api/routes/reports.py"
    source = dict(_route_sources())[rel]
    assert not _audit([(rel, source)]).findings
    pair = (
        "    require_user_token(current_user)\n"
        "    _check_report_rate_limit(current_user.user_id)"
    )
    assert pair in source
    mutated = source.replace(pair, "    _check_report_rate_limit(current_user.user_id)", 1)
    findings = _audit([(rel, mutated)]).findings
    assert findings and any("_check_report_rate_limit" in f for f in findings), (
        "removing the real report helper gate was NOT detected: " + repr(findings)
    )
