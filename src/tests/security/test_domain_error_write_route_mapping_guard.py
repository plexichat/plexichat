"""Static guard: domain errors on write routes must not degrade to a bare 500.

Background
----------
The final domain-error mapping campaign mapped client-actionable core errors
that were previously swallowed by a route's generic ``except Exception ->
500`` arm:

* N746 ``85b15153`` -- ``create_bot``: ``UserExistsError`` (409) and
  ``BotLimitExceededError`` (429) were falling through to 500.
* N758 ``5083f3b2`` -- ``create_application``: ``ApplicationLimitError`` and
  ``InvalidApplicationNameError`` were falling through to 500.
* N781 ``2153c900`` -- ``pin_message`` / ``unpin_message``: ``PinLimitError``
  and ``MessageNotPinnedError`` were falling through to 500.

A client could not tell a name collision / quota / validation failure from a
server fault, and a handler that builds its message from ``str(exc)`` risks
echoing internal detail (configured quotas, internal names, paths) into the
response body.

What this guard does
--------------------
1. Reads production source from the COMMITTED tree only (``git show
   HEAD:<path>``), so a stale working copy cannot mask a committed revert and
   cannot raise a false alarm from an in-progress peer edit. HEAD is pinned
   once per session for internal consistency.

2. Enumerates every user-facing write route (``@router.post/put/patch/delete``)
   that carries a generic ``except Exception`` arm producing a 500 response.
   The enumeration is asserted to be non-trivial and to contain all four
   audited campaign routes.

3. For each enumerated route, determines the domain exceptions its call chain
   can raise from two documented sources:
     * a service-call registry -- the audited domain-error-raising service
       functions (``create_application``, ``create_bot_for_application``,
       ``pin_message``, ``unpin_message``) mapped to the core exception classes
       they raise; and
     * the route function's own source (a domain error named in the ``try``
       body or handled by a specific ``except``/``isinstance`` map is by
       definition reachable).
   Each reachable error must be either explicitly handled with a static
   message, or covered by a documented typed/name-family global mapping
   (derived from ``error_handling.ERROR_MAPPINGS`` and
   ``exception_mapping.py``'s typed tuples, not hardcoded here). An unhandled
   and unmapped error is a violation. The response body of every error handler
   must be static -- no ``str(exc)`` / ``repr(exc)`` / f-string interpolation of
   the bound exception.

4. Fails closed on a new route: because service-call reachability is driven by
   the registry plus the function's own source (not by a blind package-wide
   import heuristic), a newly added write route that calls an audited
   domain-error-raising service with a catch-all 500 and no specific handler
   is flagged even though it was never added to any allowlist.

Self-tests (synthetic, in-memory sources) prove the detector flags a fresh
unhandled domain error, accepts globally-mapped / explicitly-handled /
static-message cases, flags a dynamic ``str(exc)`` body, and flags removing a
real handler from a real campaign route -- so the guard cannot pass vacuously.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

REPO = Path(__file__).resolve().parents[3]
WRITE_VERBS = frozenset({"post", "put", "patch", "delete"})


# ---------------------------------------------------------------------------
# Committed-tree blob reader
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


@lru_cache(maxsize=None)
def _committed(path: str) -> str:
    """Read a path from the pinned HEAD commit (never the working tree)."""
    return _git("show", f"{_head()}:{path}")


@lru_cache(maxsize=1)
def _committed_tree_paths(prefix: str) -> tuple[str, ...]:
    return tuple(
        p
        for p in _git("ls-tree", "-r", "--name-only", _head(), prefix).splitlines()
        if p.endswith(".py")
    )


# ---------------------------------------------------------------------------
# Domain-exception universe and documented global mappings
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _domain_errors() -> frozenset[str]:
    """Class names of core exceptions (``src/core/**/exceptions.py`` at HEAD)."""
    names: set[str] = set()
    for path in _committed_tree_paths("src/core"):
        if not path.endswith("exceptions.py"):
            continue
        try:
            tree = ast.parse(_committed(path))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [getattr(b, "id", getattr(b, "attr", "")) for b in node.bases]
            if any("Error" in b or "Exception" in b for b in bases):
                names.add(node.name)
    # WebSocket domain errors are raised by dispatch call chains too.
    ws = "src/websocket/exceptions.py"
    if ws in _committed_tree_paths("src/websocket"):
        try:
            for node in ast.walk(ast.parse(_committed(ws))):
                if isinstance(node, ast.ClassDef) and any(
                    "Error" in getattr(b, "id", getattr(b, "attr", ""))
                    for b in node.bases
                ):
                    names.add(node.name)
        except SyntaxError:  # pragma: no cover
            pass
    return frozenset(names)


@lru_cache(maxsize=1)
def _global_mapping_suffixes() -> frozenset[str]:
    """Suffix families from ``error_handling.ERROR_MAPPINGS`` at HEAD."""
    tree = ast.parse(_committed("src/api/middleware/error_handling.py"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ERROR_MAPPINGS" for t in node.targets
        ):
            if isinstance(node.value, ast.Dict):
                return frozenset(
                    k.value
                    for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
    raise AssertionError("ERROR_MAPPINGS not found in committed error_handling.py")


@lru_cache(maxsize=1)
def _typed_global_mappings() -> frozenset[str]:
    """Exception classes named by the typed global mapper at HEAD.

    ``exception_mapping.py`` builds typed tuples (``_NOT_FOUND_EXC`` etc.) and
    ``raise_mapped``/``map_invite_join_error`` isinstance them. Every exception
    class referenced there is a documented global mapping -- a route that
    calls ``raise_mapped`` needs no per-route arm for those classes.
    """
    tree = ast.parse(_committed("src/api/routes/exception_mapping.py"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.endswith("Error"):
            names.add(node.attr)
        elif isinstance(node, ast.Name) and node.id.endswith("Error"):
            names.add(node.id)
    return frozenset(names)


def _is_globally_mapped(name: str) -> bool:
    if name in _typed_global_mappings():
        return True
    return any(name.endswith(sfx) for sfx in _global_mapping_suffixes())


# ---------------------------------------------------------------------------
# Documented call-chain reachability: audited domain-error-raising services
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainService:
    """A service function whose call chain raises the listed domain errors."""

    call: str  # method name as called from the route (``applications.X``)
    errors: tuple[str, ...]
    why: str


SERVICES: tuple[DomainService, ...] = (
    DomainService(
        "create_application",
        (
            "ApplicationLimitError",
            "InvalidApplicationNameError",
            "ApplicationNotFoundError",
            "ApplicationAccessDeniedError",
        ),
        "core.applications.manager.application_crud.create_application raises "
        "quota/name/ownership errors; N758 mapped the first two.",
    ),
    DomainService(
        "create_bot_for_application",
        (
            "UserExistsError",
            "BotLimitExceededError",
            "ApplicationNotFoundError",
            "ApplicationAccessDeniedError",
        ),
        "core.applications.manager.application_crud.create_bot_for_application "
        "delegates to auth.create_bot (duplicate name / bot quota); N746 mapped "
        "the first two.",
    ),
    DomainService(
        "pin_message",
        (
            "PinLimitError",
            "MessageNotFoundError",
            "ConversationAccessDeniedError",
        ),
        "core.messaging.services.pin.PinService.pin_message raises the pin "
        "quota error plus not-found/access errors; N781 mapped PinLimitError.",
    ),
    DomainService(
        "unpin_message",
        (
            "MessageNotPinnedError",
            "MessageNotFoundError",
            "ConversationAccessDeniedError",
        ),
        "core.messaging.services.pin.PinService.unpin_message raises "
        "MessageNotPinnedError when the message is not pinned; N781 mapped it.",
    ),
)

SERVICE_BY_CALL = {svc.call: svc for svc in SERVICES}

# The four audited campaign routes, so the enumeration and service registry
# can be proven non-vacuous against the committed tree.
AUDITED_ROUTES: tuple[tuple[str, str, str], ...] = (
    ("src/api/routes/applications.py", "create_application", "create_application"),
    ("src/api/routes/applications.py", "create_bot", "create_bot_for_application"),
    ("src/api/routes/messages/pins.py", "pin_message", "pin_message"),
    ("src/api/routes/messages/pins.py", "unpin_message", "unpin_message"),
)


# ---------------------------------------------------------------------------
# AST analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    route: str
    kind: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        return f"{self.route}: {self.kind}: {self.detail}"


def _except_names(handler: ast.ExceptHandler) -> set[str]:
    """Exception class names named by an ``except`` clause."""
    t = handler.type
    if t is None:
        return {"BaseException"}
    if isinstance(t, ast.Name):
        return {t.id}
    if isinstance(t, ast.Attribute):
        return {t.attr}
    if isinstance(t, ast.Tuple):
        out: set[str] = set()
        for elt in t.elts:
            out |= _except_names(ast.ExceptHandler(type=elt, name=None, body=[]))
        return out
    return set()


def _is_broad(handler: ast.ExceptHandler) -> bool:
    return bool(_except_names(handler) & {"Exception", "BaseException"})


def _bound_names(handler: ast.ExceptHandler) -> set[str]:
    return {handler.name} if handler.name else set()


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _uses_bound_exception(node: ast.AST, bound: set[str]) -> bool:
    """True when ``node`` interpolates a caught exception into a value."""
    if not bound:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in bound:
            return True
        if isinstance(sub, ast.Call):
            name = _call_name(sub)
            if name in {"str", "repr", "format"} and any(
                isinstance(a, ast.Name) and a.id in bound for a in sub.args
            ):
                return True
    return False


def _message_fields(call: ast.Call) -> list[ast.AST]:
    """Value nodes that carry a client-visible error message in a response."""
    fields: list[ast.AST] = []
    if _call_name(call) in {"HTTPException", "JSONResponse", "PlainTextResponse"}:
        for kw in call.keywords:
            if kw.arg in {"detail", "content"}:
                fields.append(kw.value)
        # HTTPException(500, detail=...) also accepts ``detail`` positionally.
        if not fields and len(call.args) >= 2:
            fields.append(call.args[1])
    return fields


def _legacy_name_mapped(err: str, handler: ast.ExceptHandler) -> bool:
    """Whether a legacy name-sniffing arm covers ``err`` with a static 4xx.

    Some older routes map a domain error by matching a substring of
    ``type(e).__name__`` (e.g. ``"Owner" in exc_name`` for
    ``CannotModifyOwnerError``) and raise a static 4xx. That is not a *typed*
    mapping, but it is an explicit, reviewed handler -- flagging it here would
    be a false positive. This only recognises an actual literal present in the
    catch-all handler; a brand-new error with no such arm still fails closed.
    """
    literals = {
        n.value
        for n in ast.walk(handler)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    return any(s and len(s) >= 3 and s in err for s in literals)


# Dynamic-message findings are owned by the existing whole-tree ratchet
# ``test_client_error_message_disclosure_ratchet.py``. These three sites are
# already recorded in that guard's ALLOWED_DYNAMIC_ERROR_MESSAGES; defer to it
# rather than double-reporting the same reviewed disclosure decisions here.
KNOWN_DYNAMIC_MESSAGE_SITES: frozenset[str] = frozenset(
    {
        "src/api/routes/avatars/routes.py::delete_server_icon",
        "src/api/routes/features/tier_features.py::update_user_features",
        "src/api/routes/servers/channels.py::create_server_channel",
    }
)


def _dynamic_message_violations(handler: ast.ExceptHandler, source: str) -> list[str]:
    """Client-visible messages in a handler that interpolate the exception."""
    bound = _bound_names(handler)
    out: list[str] = []
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        for field in _message_fields(node):
            if _uses_bound_exception(field, bound):
                segment = " ".join(
                    (ast.get_source_segment(source, field) or "<expr>").split()
                )
                out.append(segment)
    return out


def _is_write_route(fn: ast.AST) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if dec.func.attr.lower() in WRITE_VERBS:
                return True
    return False


def _is_500_response(handler: ast.ExceptHandler) -> bool:
    """Whether a catch-all handler builds a 500 response."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Constant) and node.value == 500:
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "HTTP_500_INTERNAL_SERVER_ERROR"
        ):
            return True
    return False


@dataclass(frozen=True)
class RouteInfo:
    route: str
    fn_name: str
    catch_all: tuple[ast.ExceptHandler, ...]
    specific_errors: frozenset[str]
    reachable: frozenset[str]


def _route_info(source: str, rel_path: str, tree: ast.Module, fn) -> RouteInfo:
    """Analyze one write-route function with a catch-all 500 arm."""
    catch_all = tuple(
        h
        for tr in ast.walk(fn)
        if isinstance(tr, ast.Try)
        for h in tr.handlers
        if _is_broad(h) and _is_500_response(h)
    )
    specific: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and not _is_broad(node):
            specific |= {e for e in _except_names(node) if e in _domain_errors()}

    reachable: set[str] = set()
    # (a) Service-call reachability from the documented registry.
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            svc = SERVICE_BY_CALL.get(_call_name(node))
            if svc:
                reachable.update(svc.errors)
    # (b) Function-source reachability: a domain error named in the try body
    #     or mapped by an isinstance() in the catch-all arm.
    for tr in ast.walk(fn):
        if not isinstance(tr, ast.Try):
            continue
        for stmt in tr.body:
            seg = ast.get_source_segment(source, stmt) or ""
            for e in _domain_errors():
                if e in seg:
                    reachable.add(e)
    for h in catch_all:
        seg = ast.get_source_segment(source, h) or ""
        for e in _domain_errors():
            if e in seg:
                reachable.add(e)
    return RouteInfo(
        f"{rel_path}::{fn.name}",
        fn.name,
        catch_all,
        frozenset(specific),
        frozenset(reachable),
    )


def analyze_source(
    source: str, rel_path: str, *, check_all_handlers: bool = False
) -> tuple[list[RouteInfo], list[Violation]]:
    """Return (route infos, violations) for one route module source.

    ``check_all_handlers`` extends the static-message check from the catch-all
    arm to every ``except`` in the route. The whole-tree sweep keeps the narrow
    default (dynamic 4xx messages tree-wide are owned by the disclosure
    ratchet); audits and self-tests opt into the full check.
    """
    tree = ast.parse(source)
    infos: list[RouteInfo] = []
    violations: list[Violation] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_write_route(fn):
            continue
        info = _route_info(source, rel_path, tree, fn)
        if not info.catch_all:
            continue
        infos.append(info)

        covered = info.specific_errors | {
            e for e in info.reachable if _is_globally_mapped(e)
        }
        # Legacy name-sniffing arms in a catch-all count as explicit handling.
        covered |= {
            e
            for e in info.reachable
            if any(_legacy_name_mapped(e, h) for h in info.catch_all)
        }
        for err in sorted(info.reachable - covered):
            violations.append(
                Violation(
                    info.route,
                    "unmapped-domain-error",
                    f"{err} reachable via service call / try source but neither "
                    "explicitly handled nor covered by a typed global mapping",
                )
            )
        handlers_to_check = (
            [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)]
            if check_all_handlers
            else list(info.catch_all)
        )
        for h in handlers_to_check:
            if info.route in KNOWN_DYNAMIC_MESSAGE_SITES:
                continue  # owned by the disclosure ratchet (see constant)
            for msg in _dynamic_message_violations(h, source):
                violations.append(
                    Violation(
                        info.route,
                        "dynamic-error-message",
                        f"500 handler interpolates the caught exception: {msg}",
                    )
                )
    return infos, violations


@lru_cache(maxsize=1)
def _whole_tree_analysis() -> tuple[tuple[RouteInfo, ...], tuple[Violation, ...]]:
    infos: list[RouteInfo] = []
    violations: list[Violation] = []
    for path in _committed_tree_paths("src/api/routes"):
        source = _committed(path)
        if "except" not in source or "router." not in source:
            continue
        try:
            i, v = analyze_source(source, path)
        except SyntaxError:  # pragma: no cover - defensive
            continue
        infos.extend(i)
        violations.extend(v)
    return tuple(infos), tuple(violations)


# ---------------------------------------------------------------------------
# Tests over the committed tree
# ---------------------------------------------------------------------------


def test_enumeration_is_nontrivial_and_contains_campaign_routes():
    """The sweep really enumerates write routes with a generic 500 arm."""
    infos, _ = _whole_tree_analysis()
    keys = {(i.route.split("::")[0], i.fn_name) for i in infos}
    assert len(infos) > 100, (
        f"enumerated only {len(infos)} write routes with a catch-all 500; "
        "the detector/prefilter went blind"
    )
    for path, func, _service in AUDITED_ROUTES:
        assert (path, func) in keys, (
            f"audited campaign route {path}::{func} not enumerated -- the "
            "guard is not covering the fixes it is meant to pin"
        )


def test_service_registry_is_reachable_on_committed_tree():
    """Premise: each documented service call is present in its audited route."""
    for path, func, service in AUDITED_ROUTES:
        source = _committed(path)
        tree = ast.parse(source)
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
        )
        calls = {_call_name(n) for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert service in calls, f"{path}::{func} no longer calls {service}()"


def test_no_enumerated_route_has_an_unmapped_or_dynamic_domain_error():
    """Core lock: a new unhandled domain-error write route fails closed."""
    _, violations = _whole_tree_analysis()
    assert not violations, (
        "Domain-error mapping violations on committed HEAD:\n  "
        + "\n  ".join(str(v) for v in violations)
        + "\nExplicitly handle the error with a static message, cover it with "
        "a typed global mapping, or document a reviewed verdict."
    )


def test_each_campaign_route_handles_its_call_chain_errors():
    """The three N746/N758/N781 fixes are genuinely present and static."""
    by_route = {
        (i.route.split("::")[0], i.fn_name): i for i in _whole_tree_analysis()[0]
    }
    expectations = {
        ("src/api/routes/applications.py", "create_application"): (
            "ApplicationLimitError",
            "InvalidApplicationNameError",
        ),
        ("src/api/routes/applications.py", "create_bot"): (
            "UserExistsError",
            "BotLimitExceededError",
        ),
        ("src/api/routes/messages/pins.py", "pin_message"): ("PinLimitError",),
        ("src/api/routes/messages/pins.py", "unpin_message"): (
            "MessageNotPinnedError",
        ),
    }
    for key, errors in expectations.items():
        info = by_route[key]
        for err in errors:
            assert err in info.specific_errors, (
                f"{info.route}: {err} is no longer explicitly handled -- a "
                "client-actionable domain error would degrade to 500"
            )
        # Every handler on the audited routes must use a static response body.
        source = _committed(key[0])
        _, routed_violations = analyze_source(source, key[0], check_all_handlers=True)
        dynamic = [v for v in routed_violations if v.kind == "dynamic-error-message"]
        assert not dynamic, (
            f"{info.route}: domain-error handler interpolates the exception "
            f"into a client-visible body: {dynamic}"
        )


# ---------------------------------------------------------------------------
# Non-vacuity self-tests (synthetic, in-memory sources)
# ---------------------------------------------------------------------------


def _route_source(body: str, exc: str = "Exception") -> str:
    return (
        "from fastapi import APIRouter, HTTPException\n"
        "router = APIRouter()\n"
        "@router.post('/new')\n"
        "async def new_route(current_user=Depends(get_current_user)):\n"
        "    try:\n"
        f"{body}\n"
        f"    except {exc}:\n"
        "        raise HTTPException(500, detail={'error': {'message': 'Internal server error'}})\n"
    )


def test_synthetic_unhandled_domain_error_is_flagged():
    """A NEW route calling an audited service with no handler is flagged."""
    source = _route_source(
        "        result = applications.create_bot_for_application(user_id=1, application_id=2)"
    )
    _, violations = analyze_source(source, "new_route.py")
    kinds = {(v.kind, v.detail.split()[0]) for v in violations}
    assert (
        "unmapped-domain-error",
        "BotLimitExceededError",
    ) in kinds, "fresh unhandled BotLimitExceededError was NOT flagged"
    assert any(v.kind == "unmapped-domain-error" for v in violations)


def test_synthetic_mapped_and_static_cases_pass():
    """Globally-mapped errors and explicit static handlers are accepted."""
    # (a) ApplicationLimitError is globally mapped (LimitError family).
    mapped = _route_source("        app = applications.create_application(name='x')")
    # create_application also raises InvalidApplicationNameError (unmapped),
    # so handle it explicitly with a static message.
    mapped = mapped.replace(
        "    except Exception:\n",
        "    except InvalidApplicationNameError:\n"
        "        raise HTTPException(400, detail={'error': {'message': 'Invalid application name'}})\n"
        "    except Exception:\n",
    )
    assert analyze_source(mapped, "new_route.py", check_all_handlers=True)[1] == []

    # (b) Every reachable error of the service is explicitly handled with a
    #     static message -> no violation.
    explicit = _route_source("        x = messaging.unpin_message(1, 2)")
    explicit = explicit.replace(
        "    except Exception:\n",
        "    except MessageNotPinnedError:\n"
        "        raise HTTPException(400, detail={'error': {'message': 'Not pinned'}})\n"
        "    except Exception:\n",
    )
    assert analyze_source(explicit, "new_route.py", check_all_handlers=True)[1] == []


def test_synthetic_dynamic_message_is_flagged():
    """A handler that interpolates str(exc) into the body is flagged."""
    source = (
        "from fastapi import APIRouter, HTTPException\n"
        "router = APIRouter()\n"
        "@router.post('/new')\n"
        "async def new_route():\n"
        "    try:\n"
        "        x = messaging.unpin_message(1, 2)\n"
        "    except MessageNotPinnedError as exc:\n"
        "        raise HTTPException(400, detail={'error': {'message': str(exc)}})\n"
        "    except Exception:\n"
        "        raise HTTPException(500, detail={'error': {'message': 'Internal server error'}})\n"
    )
    _, violations = analyze_source(source, "new_route.py", check_all_handlers=True)
    assert any(v.kind == "dynamic-error-message" for v in violations), (
        "str(exc) in a handler body was NOT flagged"
    )


def test_detector_flags_removing_a_real_campaign_handler():
    """Non-vacuity against real code: drop BotLimitExceededError arm -> fail."""
    source = _committed("src/api/routes/applications.py")
    tree = ast.parse(source)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "create_bot"
    )
    handler = next(
        h
        for h in ast.walk(fn)
        if isinstance(h, ast.ExceptHandler)
        and "BotLimitExceededError" in _except_names(h)
    )
    segment = ast.get_source_segment(source, handler)
    assert segment and "BotLimitExceededError" in segment  # precondition
    weakened = source.replace(segment, "", 1)
    ast.parse(weakened)  # the mutation must stay syntactically valid
    _, violations = analyze_source(weakened, "src/api/routes/applications.py")
    assert any(
        v.kind == "unmapped-domain-error" and "BotLimitExceededError" in v.detail
        for v in violations
    ), "removing the real N746 handler was NOT flagged"
