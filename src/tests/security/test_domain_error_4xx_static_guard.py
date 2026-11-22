"""Committed-tree static guard: domain errors on write routes -> static 4xx.

Follow-up to the domain-error->500 campaign (N746 create-bot, N758
create-application, N781 pins).  Each fix added an ``except <DomainError>`` arm
to a user-facing write route that raises a static 4xx ``HTTPException`` instead
of letting the error fall through to the generic ``except Exception -> 500``
arm (which logged ``str(exc)`` and obscured a client-actionable outcome).

Why a new guard is needed
-------------------------
* ``test_client_error_message_disclosure_ratchet.py`` is a working-tree,
  whole-tree AST ratchet that only flags *dynamic* exception-derived bodies.
  It cannot notice a domain error that silently falls through to a static
  generic 500, and it reads the working tree rather than the committed tree.
* the per-route behavioural tests (``test_create_bot_domain_errors_route.py``,
  ``test_create_application_domain_error_route.py``,
  ``test_pin_limit_error_route.py``) drive the live route at test time but do
  not pin the committed tree; a stale staged whole-file revert is invisible to
  them until someone notices the behaviour change.

This guard pins each (route, domain error, status, static message) pair against
the blob in HEAD via ``git show``.  A stale/gutted staged file therefore cannot
mask a revert.  The AST evaluator, run over the committed blob:

  1. finds the named route function and an ``except <DomainError>`` handler in
     it (a bare ``except Exception`` / 500 arm does NOT count);
  2. confirms the handler raises ``HTTPException`` with the expected 4xx
     status (numeric ``400`` or ``status.HTTP_409_CONFLICT`` both accepted);
  3. confirms the ``detail`` body is a pure literal dict whose
     ``error.message`` equals the pinned static message and whose ``error.code``
     equals the status -- a ``str(exc)`` call, f-string or tainted local makes
     the body non-literal and is rejected (no raw ``str(exc)`` in the body);

Non-vacuity is proven three ways: a synthetic source with the domain-error arm
removed (so it falls to 500) is flagged; a synthetic source with ``str(exc)``
in the body is flagged; and a synthetic correct source is accepted.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

pytestmark = [pytest.mark.security, pytest.mark.unit]


@dataclass(frozen=True)
class RouteErrorMapping:
    """One pinned (user-facing write route, domain error -> static 4xx) pair."""

    key: str
    path: str
    func: str
    error: str
    status: int
    message: str
    why: str


# Registry of the fixed routes from the domain-error->500 campaign.  Keys embed
# the finding id so a companion set can prove none is silently dropped.
ROUTE_ERROR_MAPPINGS: tuple[RouteErrorMapping, ...] = (
    RouteErrorMapping(
        "n746-create-bot-user-exists",
        "src/api/routes/applications.py",
        "create_bot",
        "UserExistsError",
        409,
        "Bot username already taken",
        "auth.create_bot raises UserExistsError on a duplicate bot username; "
        "without an arm it fell to generic 500 and echoed str(exc).",
    ),
    RouteErrorMapping(
        "n746-create-bot-limit",
        "src/api/routes/applications.py",
        "create_bot",
        "BotLimitExceededError",
        429,
        "Bot limit exceeded",
        "The owner's bot quota being full is a client-actionable throttle, "
        "not a server fault; static body keeps the configured limit out.",
    ),
    RouteErrorMapping(
        "n758-create-app-limit",
        "src/api/routes/applications.py",
        "create_application",
        "ApplicationLimitError",
        400,
        "Application limit reached",
        "ApplicationLimitError carries the configured quota/count; without an "
        "arm it fell to 500 and leaked that detail in the log.",
    ),
    RouteErrorMapping(
        "n758-create-app-invalid-name",
        "src/api/routes/applications.py",
        "create_application",
        "InvalidApplicationNameError",
        400,
        "Invalid application name",
        "A core-policy name rejection passed client validation; static body "
        "prevents the validator's internal reason reaching the client.",
    ),
    RouteErrorMapping(
        "n781-pin-limit",
        "src/api/routes/messages/pins.py",
        "pin_message",
        "PinLimitError",
        400,
        "Pin limit reached",
        "messaging.pin_message raises PinLimitError when the conversation pin "
        "quota is full; static body keeps quota/count internally.",
    ),
    RouteErrorMapping(
        "n781-unpin-not-pinned",
        "src/api/routes/messages/pins.py",
        "unpin_message",
        "MessageNotPinnedError",
        400,
        "Message is not pinned",
        "messaging.unpin_message raises MessageNotPinnedError for an already-"
        "unpinned target; a second domain error that used to reach 500.",
    ),
)

# Kept explicit so dropping any one mapping (or the whole block) fails the
# companion registry test as well as the per-entry parametrized checks.
DOMAIN_ERROR_PIN_KEYS: frozenset[str] = frozenset(m.key for m in ROUTE_ERROR_MAPPINGS)


class _NotStatic(Exception):
    """Body expression is not a pure literal (would leak a dynamic value)."""


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
    """Resolve HEAD once per run; pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed_source(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed blob {path}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _handler_catches(handler: ast.ExceptHandler, error: str) -> bool:
    typ = handler.type
    if isinstance(typ, ast.Name):
        return typ.id == error
    if isinstance(typ, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id == error for e in typ.elts)
    return False


def _status_value(node: ast.AST) -> int | None:
    """Resolve a status_code expression to an int (literal or status.HTTP_*)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Attribute):
        # status.HTTP_409_CONFLICT -> 409
        import re

        match = re.match(r"HTTP_(\d+)_", node.attr)
        if match:
            return int(match.group(1))
    return None


def _literal(node: ast.AST):
    """Return a pure-literal Python value for a body expression, else raise."""
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, int, float, bool, type(None))
    ):
        return node.value
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):
            raise _NotStatic("dict unpacking in body")
        return {_literal(k): _literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return type(node)(_literal(e) for e in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        return -value if isinstance(node.op, ast.USub) else +value
    # Any Call / JoinedStr / Name / Subscript / Attribute (str(exc), f"...",
    # tainted locals, ...) is a dynamic value -- reject it.
    raise _NotStatic(ast.dump(node)[:120])


def _evaluate(mapping: RouteErrorMapping, source: str) -> tuple[bool, str]:
    """Return (ok, detail); ok=False means the mapping is missing/leaky."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - defensive
        return False, f"{mapping.path}: unparseable source: {exc}"
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == mapping.func
        ),
        None,
    )
    if func is None:
        return False, f"{mapping.path}: route function {mapping.func}() not found"

    handler = next(
        (
            h
            for h in ast.walk(func)
            if isinstance(h, ast.ExceptHandler) and _handler_catches(h, mapping.error)
        ),
        None,
    )
    if handler is None:
        return False, (
            f"{mapping.path}:{mapping.func} -- domain error {mapping.error} is "
            "UNHANDLED (would fall through to the generic 500 arm)"
        )

    raises = [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Raise)
        and isinstance(node.exc, ast.Call)
        and (_call_name(node.exc) or "").endswith("HTTPException")
    ]
    if not raises:
        return False, (
            f"{mapping.path}:{mapping.func} -- {mapping.error} handler does not "
            "raise HTTPException"
        )

    last_detail = ""
    for raise_node in raises:
        call = raise_node.exc
        status_node = next(
            (kw.value for kw in call.keywords if kw.arg == "status_code"), None
        )
        status = _status_value(status_node) if status_node is not None else None
        detail_node = next(
            (kw.value for kw in call.keywords if kw.arg == "detail"), None
        )
        if detail_node is None:
            last_detail = "HTTPException has no detail body"
            continue
        try:
            body = _literal(detail_node)
        except _NotStatic as exc:
            return False, (
                f"{mapping.path}:{mapping.func} -- {mapping.error} body is not a "
                f"static literal (raw str(exc)/f-string/dynamic value would leak "
                f"to the client): {exc}"
            )
        error_obj = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error_obj, dict):
            last_detail = "detail body has no error object"
            continue
        message = error_obj.get("message")
        code = error_obj.get("code")
        if status != mapping.status:
            last_detail = f"status {status!r} != expected {mapping.status}"
            continue
        if code != mapping.status:
            last_detail = f"body code {code!r} != expected {mapping.status}"
            continue
        if message != mapping.message:
            last_detail = f"message {message!r} != expected {mapping.message!r}"
            continue
        return True, (
            f"{mapping.path}:{mapping.func} maps {mapping.error} -> {status} "
            f"with static message {message!r}"
        )
    return (
        False,
        f"{mapping.path}:{mapping.func} -- {mapping.error} arm mismatch: {last_detail}",
    )


# --- committed-tree presence checks -----------------------------------------


@pytest.mark.parametrize(
    "mapping", ROUTE_ERROR_MAPPINGS, ids=[m.key for m in ROUTE_ERROR_MAPPINGS]
)
def test_domain_error_mapping_present_at_head(
    mapping: RouteErrorMapping, commit_sha: str
) -> None:
    """Every pinned domain error maps to its static 4xx in the committed tree."""
    source = _committed_source(commit_sha, mapping.path)
    ok, detail = _evaluate(mapping, source)
    assert ok, f"{mapping.key}: {detail}"


def test_registry_is_complete_and_well_formed() -> None:
    """The registry is unique, documented and cannot be silently thinned."""
    keys = [m.key for m in ROUTE_ERROR_MAPPINGS]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate mapping keys"
    assert DOMAIN_ERROR_PIN_KEYS == set(keys)
    assert len(DOMAIN_ERROR_PIN_KEYS) == 6
    for mapping in ROUTE_ERROR_MAPPINGS:
        assert mapping.path.startswith("src/api/routes/"), mapping.key
        assert 400 <= mapping.status < 500, f"{mapping.key} is not a 4xx mapping"
        assert mapping.message, f"{mapping.key} has an empty static message"
        assert mapping.why, f"{mapping.key} is undocumented"


def test_route_functions_are_the_expected_paths() -> None:
    """Each key still points at the route file the campaign actually fixed."""
    by_key = {m.key: m for m in ROUTE_ERROR_MAPPINGS}
    assert (
        by_key["n746-create-bot-user-exists"].path == "src/api/routes/applications.py"
    )
    assert by_key["n746-create-bot-limit"].func == "create_bot"
    assert by_key["n758-create-app-invalid-name"].func == "create_application"
    assert by_key["n781-pin-limit"].path == "src/api/routes/messages/pins.py"
    assert by_key["n781-unpin-not-pinned"].func == "unpin_message"


# --- non-vacuity: the evaluator must reject every broken form ----------------

_SYNTH_CORRECT = """
def {func}():
    try:
        do_work()
    except {error}:
        raise HTTPException(
            status_code={status},
            detail={{"error": {{"code": {status}, "message": "{message}"}}}},
        )
"""

_SYNTH_UNHANDLED = """
def {func}():
    try:
        do_work()
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={{"error": {{"code": 500, "message": "Internal server error"}}}},
        )
"""

_SYNTH_LEAK = """
def {func}():
    try:
        do_work()
    except {error} as exc:
        raise HTTPException(
            status_code={status},
            detail={{"error": {{"code": {status}, "message": str(exc)}}}},
        )
"""


@pytest.mark.parametrize(
    "mapping", ROUTE_ERROR_MAPPINGS, ids=[m.key for m in ROUTE_ERROR_MAPPINGS]
)
def test_synthetic_correct_mapping_is_accepted(mapping: RouteErrorMapping) -> None:
    """A correct hand-built handler is accepted (guard is not always-failing)."""
    source = _SYNTH_CORRECT.format(
        func=mapping.func,
        error=mapping.error,
        status=mapping.status,
        message=mapping.message,
    )
    ok, detail = _evaluate(mapping, source)
    assert ok, detail


@pytest.mark.parametrize(
    "mapping", ROUTE_ERROR_MAPPINGS, ids=[m.key for m in ROUTE_ERROR_MAPPINGS]
)
def test_synthetic_unhandled_domain_error_is_flagged(
    mapping: RouteErrorMapping,
) -> None:
    """Non-vacuity: dropping the domain arm (falls to 500) must be flagged."""
    source = _SYNTH_UNHANDLED.format(func=mapping.func)
    ok, detail = _evaluate(mapping, source)
    assert not ok, "a domain error falling through to 500 was not caught"
    assert mapping.error in detail and "UNHANDLED" in detail


@pytest.mark.parametrize(
    "mapping", ROUTE_ERROR_MAPPINGS, ids=[m.key for m in ROUTE_ERROR_MAPPINGS]
)
def test_synthetic_str_exc_in_body_is_flagged(mapping: RouteErrorMapping) -> None:
    """Non-vacuity: a raw str(exc) response body must be flagged."""
    source = _SYNTH_LEAK.format(
        func=mapping.func, error=mapping.error, status=mapping.status
    )
    ok, detail = _evaluate(mapping, source)
    assert not ok, "a raw str(exc) response body was not caught"
    assert "static literal" in detail and "str(exc)" in detail
