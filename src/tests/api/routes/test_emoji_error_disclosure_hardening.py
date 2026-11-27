"""Regression coverage for CWE-209 in the custom-emoji routes.

The emoji route module built several 4xx/5xx ``detail`` bodies by interpolating
``str(e)``. A caller who could provoke an internal failure (malformed upload,
database error, misconfigured storage) received the raw exception text in the
HTTP response -- SQL fragments, filesystem paths, connection details. That is
information exposure through an error message and an outlier in this codebase,
where the other route modules return a stable ``Internal server error`` and
keep the exception detail in the server-side log only.

These tests pin both halves of the fix:

* a static invariant over every ``HTTPException`` in the module -- no dynamic
  value may reach a response ``detail`` (future additions cannot regress);
* a behavioural test that forces a mutation to fail with a canary-bearing
  exception and checks the canary is absent from the surfaced detail.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

_EMOJI_ROUTES = (
    Path(__file__).resolve().parents[4] / "src" / "api" / "routes" / "emojis.py"
)


@pytest.mark.security
@pytest.mark.unit
def test_emoji_route_error_details_are_all_static() -> None:
    """No HTTPException in the emoji routes may interpolate any value.

    Logging calls are allowed to carry exception text (operators need it);
    only the client-visible ``detail`` keyword is constrained here. Strings,
    numbers and container literals are fine -- f-strings, ``str(...)`` calls
    and bare name references (e.g. ``detail=e``) are not.
    """
    tree = ast.parse(_EMOJI_ROUTES.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "HTTPException"
        ):
            continue
        detail = next(
            (kw.value for kw in node.exc.keywords if kw.arg == "detail"), None
        )
        if detail is None:
            continue
        for part in ast.walk(detail):
            if isinstance(part, (ast.JoinedStr, ast.FormattedValue)):
                offenders.append(f"line {node.lineno}: interpolated detail")
            elif isinstance(part, ast.Call) and isinstance(part.func, ast.Name):
                if part.func.id in {"str", "repr", "format"}:
                    offenders.append(f"line {node.lineno}: converted detail")
    assert offenders == [], offenders


@pytest.mark.security
@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_emoji_failure_does_not_echo_exception_text(monkeypatch) -> None:
    """A failing emoji mutation returns a generic detail, not ``str(e)``."""
    import src.api as api
    from src.api.routes import emojis
    from src.api.schemas.emojis import EmojiUpdateRequest

    canary = "CANARY_SQLSTATE_575f3c_password=postgres://internal"
    reactions = MagicMock()
    # The route binds the emoji to the URL's server before mutating it, so the
    # lookup has to resolve to an emoji owned by that server; otherwise the
    # 404 binding check short-circuits the mutation this test is exercising.
    reactions.get_custom_emoji.return_value = SimpleNamespace(server_id="123456789")
    reactions.update_custom_emoji.side_effect = RuntimeError(
        f"sqlite3.OperationalError: {canary}"
    )
    servers = MagicMock()
    servers.get_permissions.return_value = {"*": True}

    monkeypatch.setattr(api, "get_reactions", lambda: reactions)
    monkeypatch.setattr(api, "get_servers", lambda: servers)

    with pytest.raises(HTTPException) as exc_info:
        await emojis.update_emoji(
            server_id="123456789",
            emoji_id="987654321",
            body=EmojiUpdateRequest(name="renamed"),
            current_user=SimpleNamespace(user_id=1),
        )

    exc = exc_info.value
    assert exc.status_code == 500
    assert canary not in str(exc.detail)
    assert exc.detail["error"]["message"] == "Internal server error"
