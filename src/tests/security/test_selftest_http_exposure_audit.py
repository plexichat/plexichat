"""Audit lock: selftest scaffolding is not HTTP-exposed and is production-disabled.

This is a proof/regression test for the bounded selftest-over-HTTP audit, not a
fix. The architecture already fails closed:

* selftest is a CLI-only runner that binds uvicorn to 127.0.0.1 and is an
  explicit operator action (``--selftest``); it registers no HTTP route;
* the only privileged signal it sets (``X-Plexichat-Internal-Secret``) is
  validated by ``api.is_self_test_request`` against a localhost peer AND a
  per-boot random 256-bit secret, compared in constant time;
* the ``selftest`` config block is disabled by default.

The public/diagnostic HTTP surface (``/health``, ``/ready``, ``/status``,
``/version``, ``/capabilities``, ``/openapi.json``) is read-only; admin
diagnostics are host-restricted and scope-gated.

If any of these invariants ever regresses, this test fails loudly so the
exposure cannot be reintroduced silently.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import src.api as api
from src.config_defaults import get_default_config

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Route/decorator names that would register an HTTP or WS endpoint.
_REGISTRARS = {
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "api_route",
    "websocket",
    "mount",
    "include_router",
    "add_api_route",
    "add_websocket_route",
}

# Deliberately specific to selftest scaffolding: a bare "test" substring would
# match legitimate endpoints such as the admin plexijoin connection test.
_SELFTEST_NEEDLES = ("selftest", "self_test", "self-test", "_selftest")


def _request(host: str, headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        client=SimpleNamespace(host=host), headers=headers or {}
    )


def test_selftest_disabled_by_default() -> None:
    """Production defaults must not run selftest or start it at boot."""
    selftest = get_default_config()["selftest"]
    assert selftest["enabled"] is False
    assert selftest["run_on_startup"] is False


def test_internal_secret_gate_fails_closed(monkeypatch) -> None:
    """A selftest request needs BOTH localhost peer and the random secret."""
    secret = "s" + "3" * 63
    header = {"X-Plexichat-Internal-Secret": secret}

    monkeypatch.setattr(api, "_internal_secret", secret)
    # Wrong peer with a correct secret -> not internal.
    assert api.is_self_test_request(_request("10.0.0.1", header)) is False
    # Correct peer, no secret header -> not internal.
    assert api.is_self_test_request(_request("127.0.0.1")) is False
    # Correct peer, wrong secret -> not internal.
    assert api.is_self_test_request(
        _request("127.0.0.1", {"X-Plexichat-Internal-Secret": "wrong"})
    ) is False
    # Only the full signal (localhost + secret) is accepted.
    assert api.is_self_test_request(_request("127.0.0.1", header)) is True

    # No provisioned secret (e.g. before init) -> always fails closed.
    monkeypatch.setattr(api, "_internal_secret", None)
    assert api.is_self_test_request(_request("127.0.0.1", header)) is False


def test_no_selftest_http_route_registered() -> None:
    """No route decorator/mount anywhere in the API may expose a selftest path."""
    offenders: list[tuple[str, int, str]] = []
    roots = [
        _REPO_ROOT / "src" / "api" / "routes",
        _REPO_ROOT / "src" / "api" / "app.py",
    ]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(root.rglob("*.py"))
        elif root.is_file():
            files.append(root)

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                continue
            if name not in _REGISTRARS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                lowered = first.value.lower()
                if any(needle in lowered for needle in _SELFTEST_NEEDLES):
                    offenders.append(
                        (str(path.relative_to(_REPO_ROOT)), node.lineno, first.value)
                    )

    assert not offenders, f"selftest HTTP route(s) registered: {offenders}"
