"""Client-visible error messages must not interpolate a caught exception.

Follow-up to ``99352505`` (N524): the global handler ``_sanitize_error_message``
scrubs SQL keywords and 5xx, but a 4xx ``detail`` reaches the client verbatim.
``format_error_response`` / the HTTPException middleware therefore cannot save
a handler that builds its own message from ``str(e)`` / ``repr(e)`` / an
f-string of the caught exception -- an ``OSError`` there carries an absolute
filesystem path, a validation error can echo a secret, a generic exception can
echo a stack fragment (CWE-209).

This is a whole-tree, AST-only ratchet over ``src/api/routes/**``. It enumerates
every client-visible error construction (``HTTPException`` ``detail``, an
``{"error": {"message": ...}}`` body, and ``JSONResponse`` /
``PlainTextResponse`` content) whose message is dynamic *because it interpolates
the exception bound by an ``except`` block*. Exception names are discovered from
the tree itself (``except X as <name>``), so a brand-new handler using a novel
variable name is flagged automatically rather than slipping past a hardcoded
list of ``e``/``exc``.

Existing, reviewed sites are grandfathered with an explicit, per-site reason in
``ALLOWED_DYNAMIC_ERROR_MESSAGES``. Anything not in that allowlist fails the
build. The detector is validated against synthetic sources before it is trusted
over the tree, so a guard that never fires cannot pass vacuously.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
ROUTES = ROOT / "src/api/routes"

# Calls that carry a client-visible error message. ``HTTPException`` is matched
# by suffix so FastAPI's and Starlette's class (and any aliased import) are
# covered. The response classes cover handlers that return a body directly,
# which bypasses the exception-middleware sanitizer entirely.
_RESPONSE_CLASSES = frozenset(
    {"JSONResponse", "PlainTextResponse", "HTMLResponse", "Response", "Content"}
)
_STR_LIKE = ("str", "repr", "format")


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
    elif isinstance(node, ast.Attribute):
        return node.attr
    else:
        return None
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _source(src: str, node: ast.AST | None) -> str:
    if node is None:
        return "<none>"
    try:
        segment = ast.get_source_segment(src, node)
    except Exception:  # pragma: no cover - defensive, ast always has the node
        segment = None
    return " ".join((segment or "<expr>").split())


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _enclosing_except(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, ast.ExceptHandler):
            return current
    return None


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return current.name
    return "<module>"


def _inside_except(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    return _enclosing_except(node, parents) is not None


def _exception_names(tree: ast.AST) -> set[str]:
    """Exception-derived names bound by ``except ... as NAME`` or a typed param.

    Discovery from the tree (rather than a hardcoded ``e``/``exc`` list) is what
    makes the guard fail closed for a new handler that picks a novel name. Typed
    parameters cover shared mappers such as ``_handle_value_error(exc: ValueError)``
    that build a client body from an exception handed to them by a route.
    """
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.name
    }
    conventional = {"e", "exc", "err", "error", "exception", "ex"}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            annotation = ""
            if arg.annotation is not None:
                try:
                    annotation = ast.unparse(arg.annotation)
                except Exception:  # pragma: no cover - defensive
                    annotation = ""
            if arg.arg in conventional or annotation.endswith(("Error", "Exception")):
                names.add(arg.arg)
    return names


def _references_exception(expr: ast.AST, names: set[str]) -> bool:
    return any(isinstance(node, ast.Name) and node.id in names for node in ast.walk(expr))


def _dynamic_exception_message(expr: ast.AST, names: set[str]) -> bool:
    """True iff ``expr`` leaks a caught exception into a client message.

    ``names`` includes exception-bound locals, exception-typed parameters and
    locals tainted by them (see ``_sensitive_names``).
    """
    if isinstance(expr, ast.Name):
        return expr.id in names  # tainted local, e.g. error_message = str(e)
    if isinstance(expr, ast.JoinedStr):  # f-string
        formatted = [n.value for n in expr.values if isinstance(n, ast.FormattedValue)]
        if not formatted:
            return False
        for value in formatted:
            if _references_exception(value, names):
                return True
            # e.g. f"Deletion failed: {str(e)}" / {exc!r}
            if isinstance(value, ast.Call) and _call_name(value) in _STR_LIKE:
                return True
        return False
    if isinstance(expr, ast.Call):
        name = _call_name(expr)
        if name in ("str", "repr") and expr.args and _references_exception(expr, names):
            return True
        if (
            name == "format"
            and isinstance(expr.func, ast.Attribute)
            and _references_exception(expr, names)
        ):
            return True
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Add, ast.Mod)):
        if _references_exception(expr, names):
            return True
    return False


def _sensitive_names(tree: ast.AST, src_names: set[str]) -> set[str]:
    """Fixpoint of exception names plus locals assigned from them.

    Covers the ``error_message = str(e)`` then ``detail={...: error_message}``
    shape (and ``+=`` rewrites), which a direct-expression scan would miss.

    Taint is intentionally *not* restricted to assignments lexically inside an
    ``except`` block. A shared mapper receives the exception as a typed/conventional
    parameter (``_handle_value_error(exc: ValueError)``), and a refactor can move
    ``reason = str(exc)`` outside the handler; gating taint on ``_inside_except``
    let such a "static-looking" name carry exception text into an f-string body
    and slip past the guard. Propagate from any assignment whose RHS references
    an already-sensitive name, fail closed.
    """
    names = set(src_names)
    while True:
        added: set[str] = set()
        for node in ast.walk(tree):
            value = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            elif isinstance(node, ast.AugAssign):
                value = node.value
                targets = [node.target]
            if value is None:
                continue
            if not (
                _dynamic_exception_message(value, names)
                or _references_exception(value, names)
            ):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    added.add(target.id)
        if added <= names:
            return names
        names |= added


def _error_message_values(value: ast.AST):
    """Yield (suffix, message-expression) for a dict body or a bare string."""
    if isinstance(value, ast.Dict):
        for key, val in zip(value.keys, value.values):
            if isinstance(key, ast.Constant) and key.value == "error" and isinstance(val, ast.Dict):
                for k2, v2 in zip(val.keys, val.values):
                    if isinstance(k2, ast.Constant) and k2.value == "message":
                        yield ".error.message", v2
                return
    yield "", value


def _status_source(call: ast.Call, src: str) -> str:
    for kw in call.keywords:
        if kw.arg == "status_code":
            return _source(src, kw.value)
    return "<unknown>"


def _dict_status_source(dct: ast.Dict, src: str) -> str:
    for key, val in zip(dct.keys, dct.values):
        if isinstance(key, ast.Constant) and key.value == "code":
            return _source(src, val)
    return "<unknown>"


def _status_for(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    src: str,
    body_dict: ast.Dict | None = None,
) -> str:
    """Status source for a message node: nearest enclosing call's ``status_code``.

    Walking ancestors means the same site reports the same status whether it is
    reached from the ``HTTPException`` call or from its nested body dict, so the
    two are de-duplicated into one fingerprint.
    """
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.Call):
            for kw in current.keywords:
                if kw.arg == "status_code":
                    return _source(src, kw.value)
        current = parents.get(current)
    if body_dict is not None:
        return _dict_status_source(body_dict, src)
    return "<unknown>"


@dataclass(frozen=True)
class Finding:
    relpath: str
    function: str
    status: str
    expression: str
    line: int
    kind: str

    @property
    def fingerprint(self) -> tuple[str, str, str, str]:
        """Stable identity: shifting line numbers must not invalidate entries."""
        return (self.relpath, self.function, self.status, self.expression)


def _scan_source(src: str, relpath: str) -> list[Finding]:
    tree = ast.parse(src)
    parents = _parent_map(tree)
    param_names = _exception_names(tree)
    exc_names = _sensitive_names(tree, param_names)
    # In scope when the body sits in an except block, is built by a shared
    # exception-formatting helper that takes the exception as a param, or uses a
    # name tainted by an exception (which may be assigned/used outside an except).
    def in_scope(node: ast.AST, value: ast.AST) -> bool:
        return (
            _inside_except(node, parents)
            or _references_exception(value, param_names)
            or _references_exception(value, exc_names)
        )

    findings: list[Finding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node) or ""
            if name.endswith("HTTPException"):
                detail = next(
                    (kw.value for kw in node.keywords if kw.arg == "detail"), None
                )
                if detail is not None:
                    status = _status_for(node, parents, src)
                    for suffix, value in _error_message_values(detail):
                        if _dynamic_exception_message(value, exc_names) and in_scope(node, value):
                            findings.append(
                                Finding(
                                    relpath,
                                    _enclosing_function(node, parents),
                                    status,
                                    _source(src, value),
                                    node.lineno,
                                    f"{name} detail{suffix}",
                                )
                            )
            elif name in _RESPONSE_CLASSES:
                content = node.args[0] if node.args else None
                for kw in node.keywords:
                    if kw.arg == "content":
                        content = kw.value
                if content is not None:
                    for suffix, value in _error_message_values(content):
                        if _dynamic_exception_message(value, exc_names) and in_scope(node, value):
                            findings.append(
                                Finding(
                                    relpath,
                                    _enclosing_function(node, parents),
                                    "<response-body>",
                                    _source(src, value),
                                    node.lineno,
                                    f"{name}{suffix}",
                                )
                            )

        # A body dict returned straight from a route (FastAPI serialises it) is
        # client-visible even though it is not wrapped in an explicit response.
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "error" and isinstance(val, ast.Dict):
                    status = _status_for(node, parents, src, body_dict=val)
                    for k2, v2 in zip(val.keys, val.values):
                        if isinstance(k2, ast.Constant) and k2.value == "message":
                            if _dynamic_exception_message(v2, exc_names) and in_scope(node, v2):
                                findings.append(
                                    Finding(
                                        relpath,
                                        _enclosing_function(node, parents),
                                        status,
                                        _source(src, v2),
                                        node.lineno,
                                        "dict.error.message",
                                    )
                                )

    # De-duplicate: the same expression is usually seen both on the HTTPException
    # call and on the nested dict literal. Keep one Finding per fingerprint.
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.fingerprint not in seen:
            seen.add(finding.fingerprint)
            unique.append(finding)
    return sorted(unique, key=lambda f: (f.relpath, f.line, f.kind))


def _scan_routes() -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(ROUTES.rglob("*.py")):
        relpath = str(path.relative_to(ROOT))
        src = path.read_text(encoding="utf-8")
        findings.extend(_scan_source(src, relpath))
    return findings


# Reviewed, grandfathered sites from the committed tree. Key is
# (path, function, status-source, message-expression); each value documents the
# line and *why* the interpolated exception cannot disclose a path, secret or
# stack trace. Any new key fails the build (fail closed). Note that 5xx entries
# are allowed only because ``_sanitize_error_message`` replaces every 5xx body
# with "Internal server error" before it reaches the client.
ALLOWED_DYNAMIC_ERROR_MESSAGES: dict[tuple[str, str, str, str], str] = {
    (
        "src/api/routes/admin/artifacts.py",
        "admin_set_server_retention",
        "status.HTTP_400_BAD_REQUEST",
        "str(exc)",
    ): "line 270: set_server_retention_days raises a bounded ValueError about an out-of-range retention-day count; no path, credential or stack text.",
    (
        "src/api/routes/admin/data_export.py",
        "_handle_value_error",
        "400",
        "str(exc)",
    ): "line 103 (message built at 105): shared DSAR mapper whose ValueError param comes from the route's except block; the DSAR core message is fixed ('DSAR request <id> not found') with only a caller-supplied numeric id -- no path, secret or stack frame.",
    (
        "src/api/routes/admin/roles.py",
        "create_role",
        "400",
        "str(exc)",
    ): "line 461: validate_delegable_permissions raises a fixed-vocabulary ValueError naming an unknown/wildcard scope; only role vocabulary is echoed.",
    (
        "src/api/routes/admin/roles.py",
        "update_role",
        "400",
        "str(exc)",
    ): "line 629: same bounded validate_delegable_permissions ValueError on role update.",
    (
        "src/api/routes/admin/security.py",
        "create_access_token",
        "status_code",
        "str(exc)",
    ): "line 208: create_api_access_token's ValueError is 'name already exists' or bounded name/description validation; status_code is a literal 409/400 derived from that fixed message, never a path or secret.",
    (
        "src/api/routes/admin/security.py",
        "update_access_token",
        "400",
        "str(exc)",
    ): "line 304: update_api_access_token ValueError from bounded name/description/expiry validation.",
    (
        "src/api/routes/admin/security.py",
        "rotate_access_token",
        "400",
        "str(exc)",
    ): "line 355: rotate_api_access_token ValueError for an unknown token id; fixed vocabulary.",
    (
        "src/api/routes/admin/security.py",
        "add_access_token_scope",
        "404",
        "str(exc)",
    ): "line 411: 'token/scope not found' ValueError; fixed vocabulary.",
    (
        "src/api/routes/admin/security.py",
        "add_access_token_scope",
        "400",
        "str(exc)",
    ): "line 415: other add_api_access_token_scope validation ValueError; fixed vocabulary.",
    (
        "src/api/routes/auth/login.py",
        "login",
        "400",
        "str(e)",
    ): "line 65: validate_credential_input (device_fingerprint) raises only field-name + 'required'/'exceeds maximum length'/'contains control characters'; the submitted value is never echoed.",
    (
        "src/api/routes/auth/oauth.py",
        "oauth_callback",
        "400",
        "str(exc)",
    ): "line 699: bounded OAuth field / PKCE format ValueError with a fixed message; no raw verifier or token is echoed.",
    (
        "src/api/routes/auth/password.py",
        "confirm_password_reset",
        "400",
        "str(e)",
    ): "line 165: validate_credential_input on the reset token reports only the violation class (also enumerated by the existence-oracle guard); the token value is never echoed.",
    (
        "src/api/routes/auth/register.py",
        "register",
        "400",
        "error_message",
    ): "line 110: error_message is tainted from str(e) at line 100 (InvalidUsernameError / InvalidEmailError / WeakPasswordError / AuthError -- fixed validation text) plus an optional configured password-guidance URL; no credential value, path or stack is echoed. The generic except branch uses a static 500 message.",
    (
        "src/api/routes/auth/two_factor.py",
        "complete_2fa",
        "400",
        "str(e)",
    ): "line 115: validate_credential_input on challenge_token/code reports only the violation class; the code is never echoed.",
    (
        "src/api/routes/avatars/routes.py",
        "delete_server_icon",
        "500",
        'f"Deletion failed: {str(e)}"',
    ): "line 934: 5xx only -- _sanitize_error_message replaces every 5xx body with 'Internal server error' before the client sees it.",
    (
        "src/api/routes/channels/attachments.py",
        "_upload_attachment",
        "413",
        "str(e)",
    ): "line 316: StorageQuotaExceededError.__str__ is a bounded numeric quota message; used/limit/requested bytes are returned as separate numeric fields. File is contested by another agent but this line is unchanged in HEAD.",
    (
        "src/api/routes/features/tier_features.py",
        "_feature_user_id",
        "status.HTTP_400_BAD_REQUEST",
        "str(exc)",
    ): "line 59: parse_id HardeningViolation for a malformed user id is a fixed message ('must be an integer' / 'outside the allowed range'); the raw id is not echoed.",
    (
        "src/api/routes/features/tier_features.py",
        "_validate_feature_settings",
        "status.HTTP_400_BAD_REQUEST",
        "str(exc)",
    ): "lines 82/96/109: reject_control_characters / bounded_int HardeningViolations for tier/notes/expires_at use fixed violation-class messages (lines are deduplicated into one fingerprint).",
    (
        "src/api/routes/features/tier_features.py",
        "update_user_features",
        "status.HTTP_400_BAD_REQUEST",
        'f"Invalid tier: {e}"',
    ): "line 384: InvalidTier for a caller-supplied tier token constrained to [_A-Za-z0-9_.-]{1,N}; echoes that token only, never a path, secret or stack frame.",
    (
        "src/api/routes/federation.py",
        "join_request_inbound",
        "400",
        "str(exc)",
    ): "line 762: HardeningViolation / FederationEndpointRejected from the URL shape + SSRF gate; resolve_remote_endpoint documents that the detail never includes the resolved IP or credentials. File is contested but this line is unchanged in HEAD.",
    (
        "src/api/routes/servers/channels.py",
        "create_server_channel",
        "500",
        "str(inner_e)",
    ): "line 285: 5xx only -- scrubbed to 'Internal server error' by the global handler.",
    (
        "src/api/routes/storage.py",
        "list_my_storage_files",
        "400",
        "str(exc)",
    ): "line 64: list_user_files HardeningViolation for path_prefix uses fixed messages ('path filter must be relative', '...must not contain traversal', '...exceeds the length limit'); no filesystem path is echoed.",
    (
        "src/api/routes/suite/stash/uploads.py",
        "upload_file",
        "413",
        "str(exc)",
    ): "line 118: StorageQuotaExceededError bounded numeric quota message; numeric fields are separate.",
    (
        "src/api/routes/users/profile.py",
        "update_current_user",
        "400",
        "str(ve)",
    ): "line 265: ValueError from ProfileManager profile-field validation (max social links, expiry in future, bio-content class); bounded vocabulary, no path/secret/stack.",
}


def _undocumented(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.fingerprint not in ALLOWED_DYNAMIC_ERROR_MESSAGES]


# --- guard ------------------------------------------------------------------

def test_no_undocumented_dynamic_error_message_in_routes():
    """Any newly added exception-derived client error body fails the build."""
    offenders = _undocumented(_scan_routes())
    assert not offenders, (
        "New client-visible error message interpolates a caught exception "
        "(CWE-209). Use a static message and log the detail server-side, or "
        "document a genuinely benign case in ALLOWED_DYNAMIC_ERROR_MESSAGES:\n  "
        + "\n  ".join(
            f"{f.relpath}:{f.line} {f.function} [{f.kind}] status={f.status} "
            f"message={f.expression}"
            for f in offenders
        )
    )


def test_enumeration_is_not_vacuous_and_allowlist_is_documented():
    """The scan must actually see the grandfathered sites (evidence, not air)."""
    findings = _scan_routes()
    # A real tree has well over a dozen dynamic error sites; an empty/over-
    # filtered detector would make the guard above pass trivially.
    assert len(findings) >= 15, f"only {len(findings)} dynamic error sites enumerated"
    assert len(ALLOWED_DYNAMIC_ERROR_MESSAGES) >= 15
    # Every enumerated site must be documented, and every documented entry must
    # carry a concrete reason with a line number (the (path, function/line)
    # requirement) rather than a free-pass marker.
    unknown = {f.fingerprint for f in findings} - ALLOWED_DYNAMIC_ERROR_MESSAGES.keys()
    assert not unknown, f"enumerated sites missing a reason: {sorted(unknown)}"
    for fp, reason in ALLOWED_DYNAMIC_ERROR_MESSAGES.items():
        assert reason.strip(), f"empty reason for {fp}"
        assert "line" in reason.lower(), f"reason for {fp} lacks a line reference"


def test_documented_entry_is_accepted():
    """A real, documented entry is flagged by the detector but accepted by the guard."""
    findings = _scan_routes()
    documented = [f for f in findings if f.fingerprint in ALLOWED_DYNAMIC_ERROR_MESSAGES]
    assert documented, "no documented real site was enumerated"
    sample = documented[0]
    # The detector really flags it (so acceptance is via the allowlist, not
    # because the detector is blind to it).
    assert any(
        f.fingerprint == sample.fingerprint
        for f in _scan_source(
            (ROOT / sample.relpath).read_text(encoding="utf-8"), sample.relpath
        )
    )
    assert _undocumented([sample]) == []


# --- self-tests for the detector (run before it is trusted over the tree) ----

_TEMPLATE = '''
def handler():
    try:
        do_work()
    except {exc_type} as {name}:
        raise HTTPException(
            status_code=400,
            detail={{"error": {{"code": 400, "message": {message}}}}},
        )
'''


def _synth(exc_type="OSError", name="e", message="str(e)"):
    return _TEMPLATE.format(exc_type=exc_type, name=name, message=message)


def test_detector_flags_str_e_in_400_and_accepts_static():
    """Non-vacuity: a synthetic str(e)-in-400 is flagged; a static one is not."""
    flagged = _scan_source(_synth(), "synthetic.py")
    assert len(flagged) == 1, flagged
    assert flagged[0].expression == "str(e)"
    assert flagged[0].status == "400"
    assert _undocumented(flagged), "a synthetic str(e) body must fail closed"

    static = _scan_source(_synth(message='"Failed to read file"'), "synthetic.py")
    assert static == [], f"static message must be accepted: {static}"


def test_detector_flags_fstring_repr_and_novel_exception_name():
    """A newly named exception still leaks via f-string / repr and is caught."""
    fstring = _scan_source(_synth(message='f"Failed: {e}"'), "synthetic.py")
    assert len(fstring) == 1 and fstring[0].expression == 'f"Failed: {e}"'

    repr_call = _scan_source(_synth(message="repr(e)"), "synthetic.py")
    assert len(repr_call) == 1 and repr_call[0].expression == "repr(e)"

    # The exact shape of the N524 / 99352505 leak: str(e) wrapped in an
    # f-string inside a client-visible 400. Must still be flagged.
    nested = _scan_source(_synth(message='f"Failed: {str(e)}"'), "synthetic.py")
    assert len(nested) == 1 and nested[0].expression == 'f"Failed: {str(e)}"'
    assert _undocumented(nested)

    # Novel names are discovered from the tree, so a new handler cannot dodge
    # the guard by calling its exception something other than ``e``/``exc``.
    novel = _scan_source(
        _synth(exc_type="ZorbError", name="zorbix", message="str(zorbix)"),
        "synthetic_new_route.py",
    )
    assert len(novel) == 1 and novel[0].expression == "str(zorbix)"
    assert _undocumented(novel), "a novel-name dynamic error body must fail closed"


def test_detector_flags_response_body_and_non_dict_detail():
    """Directly returned bodies bypass middleware sanitizing and are covered too."""
    response_src = '''
def handler():
    try:
        do_work()
    except Exception as e:
        return PlainTextResponse(f"oops: {e}")
'''
    response = _scan_source(response_src, "synthetic.py")
    assert len(response) == 1 and response[0].kind == "PlainTextResponse"

    bare_detail_src = '''
def handler():
    try:
        do_work()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
'''
    bare = _scan_source(bare_detail_src, "synthetic.py")
    assert len(bare) == 1 and bare[0].kind == "HTTPException detail"


def test_detector_traces_tainted_local_and_exception_param_helper():
    """Non-vacuity for the two indirect shapes found in the route sweep."""
    tainted_src = '''
def handler():
    try:
        do_work()
    except OSError as e:
        error_message = str(e)
        error_message += " guidance"
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": error_message}},
        )
'''
    tainted = _scan_source(tainted_src, "synthetic.py")
    assert len(tainted) == 1 and tainted[0].expression == "error_message"
    assert _undocumented(tainted)

    helper_src = '''
def _format_error(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": {"code": 400, "message": str(exc)}},
    )
'''
    helper = _scan_source(helper_src, "synthetic.py")
    assert len(helper) == 1 and helper[0].function == "_format_error"
    assert _undocumented(helper)


def test_fstring_static_param_exclusion_does_not_hide_exception_taint():
    """f-strings of *truly* static params stay out of scope; tainted ones do not.

    The f-string branch treats a bare-name interpolation as static unless that
    name is known to carry exception text. A "static-looking" name that is
    actually assigned from ``str(e)``/``str(exc)`` must not slip past the guard
    just because the taint assignment or the raise sits outside an ``except``
    block (a shared mapper or a refactor produces exactly that shape).
    """
    # Taint assigned inside the except, but the client body raised outside it.
    outside_raise = '''
def handler():
    try:
        do_work()
    except OSError as e:
        msg = str(e)
    raise HTTPException(
        status_code=400,
        detail={"error": {"code": 400, "message": f"Failed: {msg}"}},
    )
'''
    flagged = _scan_source(outside_raise, "synthetic.py")
    assert len(flagged) == 1 and flagged[0].expression == 'f"Failed: {msg}"'
    assert _undocumented(flagged), "a tainted f-string param must fail closed"

    # Shared mapper: exception-typed param -> local assigned outside any except.
    helper_local = '''
def _format_error(exc: ValueError):
    reason = str(exc)
    raise HTTPException(
        status_code=400,
        detail={"error": {"code": 400, "message": f"Failed: {reason}"}},
    )
'''
    flagged2 = _scan_source(helper_local, "synthetic.py")
    assert len(flagged2) == 1 and flagged2[0].expression == 'f"Failed: {reason}"'
    assert _undocumented(flagged2), "a tainted helper f-string param must fail closed"

    # Non-vacuity of the exclusion itself: a genuinely static param (a caller
    # status/field, not exception-derived) is still correctly ignored.
    static = '''
def handler(status):
    if bad(status):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Approval is {status}"}},
        )
'''
    assert _scan_source(static, "synthetic.py") == []
