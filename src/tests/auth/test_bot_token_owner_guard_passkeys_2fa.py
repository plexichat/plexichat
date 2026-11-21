"""Bot credentials must not manage their owner's passkeys or 2FA settings.

A bot token's ``user_id`` is its *human owner* (the bot id lives in
``account_id``).  The passkey and 2FA management routes operate on
``current_user.user_id`` -- registering/removing credentials and enabling or
disabling the second factor -- exactly like the session/device routes that
already call ``require_user_token``.  Without that guard a leaked bot token
can enrol an attacker-controlled passkey on the owner's account (no password
is required by the registration routes) and take the account over, or weaken
its 2FA posture.

This pins the boundary behaviourally and keeps the AST completeness invariant
in sync with the sibling auth-route suite.
"""

import ast
import asyncio
import importlib
import inspect
import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


# A bot token issued to a bot whose owner is user 100.
_BOT = _token(100, token_type="bot", account_id=500)
_OWNER = _token(100)


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


@pytest.mark.parametrize(
    "module, handler, kwargs",
    [
        ("passkeys", "passkey_register_options", {"body": None}),
        ("passkeys", "passkey_register", {"body": None, "request": None}),
        ("passkeys", "list_passkeys", {}),
        ("passkeys", "revoke_passkey", {"passkey_id": "1", "request": None}),
        ("passkeys", "rename_passkey", {"passkey_id": "1", "body": None}),
        ("two_factor", "get_2fa_status", {}),
        ("two_factor", "enable_2fa", {"body": None}),
        ("two_factor", "confirm_2fa_setup", {"body": None}),
        ("two_factor", "disable_2fa", {"body": None}),
    ],
)
def test_bot_token_rejected_on_owner_scoped_credential_routes(
    module, handler, kwargs, monkeypatch
):
    route = importlib.import_module(f"src.api.routes.auth.{module}")

    # The guard is the first statement: no auth service lookup, no DB touch.
    def _must_not_reach_service():
        raise AssertionError("bot token reached the credential service")

    monkeypatch.setattr(api, "get_auth", _must_not_reach_service)

    with pytest.raises(HTTPException) as exc:
        _call(getattr(route, handler), current_user=_BOT, **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_guard_allows_a_real_user_token():
    from src.api.routes.users.helpers import require_user_token

    assert require_user_token(_OWNER) is None


# ---------------------------------------------------------------------------
# Completeness: every new owner-scoped handler in these modules must carry the
# guard. Pre-auth handlers (challenge issuance, login completion) have no
# ``current_user`` dependency and are intentionally out of scope.
# ---------------------------------------------------------------------------

_AUTH_DIR = Path(__file__).resolve().parents[2] / "api" / "routes" / "auth"


def _iter_auth_modules():
    """Every shipped auth-route module, discovered structurally.

    The old completeness check hard-coded ``("passkeys.py", "two_factor.py")``.
    A new owner-scoped handler added to any *other* auth module (or a brand-new
    module under a novel name) was never examined, so it could key reads and
    writes off ``current_user.user_id`` without ``require_user_token`` and the
    guard passed silently.  The file list is replaced by a whole-tree sweep:
    any ``*.py`` module under ``routes/auth`` is in scope, so a new name cannot
    escape enumeration.
    """
    for path in sorted(_AUTH_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path.relative_to(_AUTH_DIR).as_posix(), path


# Floor pins only; the sweep itself is structural and needs no curated file
# list.  These assert the tree scan really reaches modules beyond the original
# two, so reverting the detector to a fixed pair of files fails non-vacuously.
_KNOWN_OWNER_SCOPED_MODULES = frozenset(
    {"passkeys.py", "two_factor.py", "sessions.py", "devices.py"}
)
_SCAN_FILES_LEGACY = frozenset({"passkeys.py", "two_factor.py"})


def _uses_caller_identity(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == "current_user"
        and child.attr == "user_id"
        for child in ast.walk(node)
    )


def _has_user_token_dependency(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    # Defaults are ``Depends(get_current_user)`` / ``Security(get_current_user)``
    # calls, so inspect the inner callable rather than the outer wrapper.
    # positional defaults align to the LAST N positional args.
    defaults = node.args.defaults
    offset = len(node.args.args) - len(defaults)
    for index, default in enumerate(defaults):
        arg = node.args.args[offset + index]
        annotation = arg.annotation
        if annotation is None:
            continue
        # ``TokenInfo`` is a plain Name; tolerate ``Optional[TokenInfo]`` too.
        annotation_id = getattr(annotation, "id", None)
        if annotation_id is None:
            annotation_id = getattr(getattr(annotation, "value", None), "id", None)
        if annotation_id != "TokenInfo":
            continue
        if not isinstance(default, ast.Call):
            continue
        if getattr(default.func, "id", None) not in ("Depends", "Security"):
            continue
        if default.args and getattr(default.args[0], "id", None) == "get_current_user":
            return True
    return False


def _calls_require_user_token(node: ast.AST) -> bool:
    """Guard calls on this handler's own execution path.

    Descend through statement-level nesting (if/for/with/try) but skip nested
    function and lambda bodies: a guard there is dead until that callable runs
    and must not mark the route as protected.
    """
    stack = [node]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(child, ast.Call)
                and getattr(child.func, "id", None) == "require_user_token"
            ):
                return True
            stack.append(child)
    return False


def _omissions_in_source(source: str, module: str):
    """Ungated owner-scoped handlers in one auth-route module's source."""
    omissions = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _uses_caller_identity(node) or not _has_user_token_dependency(node):
            continue
        if not _calls_require_user_token(node):
            omissions.append(f"{module}:{node.name}")
    return omissions


def test_every_owner_scoped_credential_handler_rejects_bot_tokens():
    omissions = []
    for name, path in _iter_auth_modules():
        omissions += _omissions_in_source(path.read_text(encoding="utf-8"), name)

    assert not omissions, (
        "owner-scoped credential handlers without require_user_token:\n  "
        + "\n  ".join(omissions)
    )


def test_structural_sweep_covers_the_whole_auth_route_tree():
    """The completeness scan is not pinned to a fixed two-file list.

    A former version scanned only ``passkeys.py`` and ``two_factor.py``.  A new
    owner-scoped handler in any other module escaped silently.  The structural
    discovery must see every shipped auth-route module, including ones the
    original list never named.
    """
    scanned = {name for name, _ in _iter_auth_modules()}
    assert _KNOWN_OWNER_SCOPED_MODULES <= scanned, (
        "auth-route sweep is missing known owner-scoped modules: "
        f"{sorted(_KNOWN_OWNER_SCOPED_MODULES - scanned)}"
    )
    # Non-vacuous: the sweep really exercises more than the two old files.
    assert scanned - _SCAN_FILES_LEGACY, (
        "auth-route sweep regressed to the old fixed file list"
    )


def test_new_owner_scoped_handler_in_novel_module_is_flagged():
    """Non-vacuity: a new module/handler under a novel name fails closed.

    The detector must not depend on knowing the module or handler name in
    advance.  A synthetic auth-route module with an owner-scoped handler that
    omits ``require_user_token`` is reported by the same helper the real sweep
    uses, even though neither the file nor the handler has ever been seen.
    """
    new_source = textwrap.dedent(
        """
        async def future_admin_export(
            current_user: TokenInfo = Depends(get_current_user),
        ):
            return {"owner": current_user.user_id}
        """
    )
    assert _omissions_in_source(
        new_source, "src/api/routes/auth/future_admin_export.py"
    ) == ["src/api/routes/auth/future_admin_export.py:future_admin_export"]

    # Same novel handler, but WITH the gate: classified, so it passes.
    gated_source = textwrap.dedent(
        """
        async def future_admin_export(
            current_user: TokenInfo = Depends(get_current_user),
        ):
            require_user_token(current_user)
            return {"owner": current_user.user_id}
        """
    )
    assert _omissions_in_source(gated_source, "future_admin_export.py") == []
