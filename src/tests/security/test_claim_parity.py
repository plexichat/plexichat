"""
Claim/config parity matrix checks.

Asserts key product/runtime claims against authoritative code/config/constants
so behavior and site/docs claims do not drift. Uses constants, route
registrations, signatures, and literal defaults from source/AST — no runtime
imports of heavy app modules and no fragile marketing-string scraping.

Claims covered:
- Gateway endpoint is /gateway (not /api/v1).
- Readiness: GET /api/v1/ready. Health: GET /api/v1/health plus /health proxy.
- PKCE S256; oauth state cookie `plexichat_oauth_state`; login rejects
  `code_verifier` (task 6 / server-side verifier secrecy).
- Ratchet opcode 50 / RATCHET_UPDATE; rotation defaults 1000 msgs or 86400s.
- DSAR endpoints /api/v1/users/@me/data-export* and 1/24h limit.
- Rate-limit defaults (global/user/ip config builders).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _func_node(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _func_arg_names(func: ast.AST) -> list[str]:
    assert isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
    return [a.arg for a in func.args.args + func.args.kwonlyargs]


def _const_assigns(tree: ast.AST) -> dict[str, object]:
    out: dict[str, object] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return out


def _int_enum_member(tree: ast.AST, class_name: str, member: str) -> int | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    t = stmt.targets[0]
                    if isinstance(t, ast.Name) and t.id == member:
                        try:
                            return int(ast.literal_eval(stmt.value))
                        except Exception:
                            return None
    return None


def _app_config_get_default(func_src: str, key: str):
    # matches app_config.get("rate_limiting.global.requests", 25000) style defaults
    m = re.search(
        r'app_config\.get\(\s*["\']' + re.escape(key) + r'["\']\s*,\s*([^)]+)\)',
        func_src,
    )
    if not m:
        return None
    try:
        return ast.literal_eval(m.group(1).strip())
    except Exception:
        return None


# === Gateway path (not /api/v1) ===


def test_gateway_websocket_path_is_root_gateway_not_api_v1():
    tree = _parse(SRC / "api/websocket/gateway.py")
    ws_paths = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if dec.func.attr == "websocket" and dec.args:
                        try:
                            ws_paths.append(ast.literal_eval(dec.args[0]))
                        except Exception:
                            pass
    assert "/gateway" in ws_paths
    assert all(not p.startswith("/api/v1") for p in ws_paths)
    # mount in app is without api prefix
    app_src = (SRC / "api/app.py").read_text(encoding="utf-8")
    assert "app.include_router(gateway_router)" in app_src
    assert (
        "prefix=config.api_prefix"
        not in app_src.split("include_router(gateway_router)")[0][-120:]
    )


# === Health / readiness + /health proxy ===


def test_health_router_defines_health_and_ready_under_api_prefix_mount():
    tree = _parse(SRC / "api/routes/health.py")
    paths = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "get"
                    and dec.args
                ):
                    try:
                        paths.append(ast.literal_eval(dec.args[0]))
                    except Exception:
                        pass
    assert "/ready" in paths
    assert "/health" in paths
    # api router is mounted at config.api_prefix -> /api/v1/ready, /api/v1/health
    app_src = (SRC / "api/app.py").read_text(encoding="utf-8")
    assert "app.include_router(api_router, prefix=config.api_prefix)" in app_src


def test_root_health_path_proxies_to_api_health_handler():
    app_src = (
        (SRC / "api/routes/../app.py").resolve().read_text(encoding="utf-8")
        if False
        else (SRC / "api/app.py").read_text(encoding="utf-8")
    )
    # root /health endpoint exists and delegates to the canonical health handler
    assert '@app.get(\n        "/health"' in app_src or '@app.get("/health"' in app_src
    assert "from .routes.health import health_check" in app_src
    assert "await health_check()" in app_src


# === PKCE S256 + oauth state cookie + login rejects code_verifier ===


def test_oauth_state_cookie_name_and_pkce_constants():
    consts = _const_assigns(_parse(SRC / "api/routes/auth/oauth.py"))
    assert consts.get("OAUTH_STATE_COOKIE") == "plexichat_oauth_state"
    # S256 only (pkce module)
    pkce = _parse(SRC / "core/auth/oauth/pkce.py")
    cls = None
    for node in ast.walk(pkce):
        if isinstance(node, ast.ClassDef) and node.name == "PKCEChallenge":
            cls = node
            break
    assert cls is not None
    method_default = None
    for stmt in cls.body:
        if (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "code_challenge_method"
        ):
            try:
                method_default = ast.literal_eval(stmt.value)
            except Exception:
                pass
    assert method_default == "S256"
    # RFC7636 challenge grammar constant present
    src = (SRC / "api/routes/auth/oauth.py").read_text(encoding="utf-8")
    assert "_PKCE_CHALLENGE_RE" in src and "43,128" in src


def test_oauth_login_rejects_code_verifier_when_provided():
    # Claim (task 6): login must reject code_verifier; the server never
    # mints/accepts the verifier at login — only the S256 challenge. The
    # parameter may exist only to enforce an explicit 400 on misuse.
    src = (SRC / "api/routes/auth/oauth.py").read_text(encoding="utf-8")
    tree = _parse(SRC / "api/routes/auth/oauth.py")
    func = _func_node(tree, "oauth_login_init")
    assert func is not None, "oauth_login_init not found"
    args = _func_arg_names(func)
    assert "code_challenge" in args and "code_challenge_method" in args
    # verifier is never a trusted input at login: reject it if supplied.
    assert "code_verifier must not be provided at the OAuth login/init step" in src
    assert "if code_verifier is not None" in src or "if code_verifier" in src
    # must not forward/store verifier from login
    assert "pkce_challenge=pkce_challenge" in src
    # accepting verifier as a usable login secret would be a regression
    assert "code_verifier" not in {a for a in args} or "must not be provided" in src


def test_oauth_login_enforces_s256_and_requires_challenge():
    src = (SRC / "api/routes/auth/oauth.py").read_text(encoding="utf-8")
    assert 'code_challenge_method not in (None, "S256")' in src
    assert "Only the S256 code_challenge_method is supported" in src
    assert "code_challenge is required for PKCE OAuth flows" in src
    assert 'params["code_challenge_method"] = "S256"' in src


# === Ratchet opcode 50 / RATCHET_UPDATE + rotation defaults ===


def test_ratchet_opcode_and_event_name_constants():
    tree = _parse(SRC / "api/websocket/opcodes.py")
    assert _int_enum_member(tree, "GatewayOpcode", "RATCHET_UPDATE") == 50


def test_ratchet_rotation_defaults_1000_msgs_or_86400s():
    consts = _const_assigns(_parse(SRC / "utils/encryption/channel_ratchet/manager.py"))
    assert consts["DEFAULT_MAX_MESSAGES_PER_INTERVAL"] == 1000
    assert consts["DEFAULT_MAX_INTERVAL_AGE_SECONDS"] == 86400
    assert consts["CONTEXT_TAG"] == b"plexichat.channel-message.v3"
    assert consts["NONCE_BYTES"] == 12
    src = (SRC / "utils/encryption/channel_ratchet/manager.py").read_text(
        encoding="utf-8"
    )
    assert 'WIRE_PREFIX = f"ENC:{WIRE_VERSION}:"' in src
    assert "WIRE_VERSION = 3" in src or "ENC:3" in src


# === DSAR endpoints and 1/24h limit ===


def test_dsar_data_export_routes_and_rate_limit():
    # route registrations
    composer_src = (SRC / "api/routes/users/composer.py").read_text(encoding="utf-8")
    for path in [
        '"/@me/data-export"',
        '"/@me/data-export/{request_id}"',
        '"/@me/data-export/{request_id}/download"',
    ]:
        assert path in composer_src, f"missing DSAR route {path}"
    # users router mounted at /users under /api/v1 -> /api/v1/users/@me/data-export*
    routes_src = (SRC / "api/routes/__init__.py").read_text(encoding="utf-8")
    assert 'prefix="/users"' in routes_src

    # 1 request per 24h on the export-creation handler
    tree = _parse(SRC / "api/routes/users/dsar.py")
    func = _func_node(tree, "request_data_export")
    assert func is not None
    found = False
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call):
            # @custom_rate_limit(config=RateLimitConfig(requests=1, window_seconds=86400))
            for kw in dec.keywords:
                if kw.arg == "config" and isinstance(kw.value, ast.Call):
                    vals = {}
                    for k in kw.value.keywords:
                        if k.arg in ("requests", "window_seconds"):
                            try:
                                vals[k.arg] = ast.literal_eval(k.value)
                            except Exception:
                                pass
                    if (
                        vals.get("requests") == 1
                        and vals.get("window_seconds") == 86400
                    ):
                        found = True
    assert found, "request_data_export must enforce 1 request per 86400s"


# === Rate-limit defaults ===


def test_ratelimit_global_user_ip_defaults():
    # config builders encode shipped defaults as app_config.get(key, <default>);
    # fallbacks must stay aligned with src/config_defaults.py (per module doc).
    src = (SRC / "core/ratelimit/config.py").read_text(encoding="utf-8")
    tree = _parse(SRC / "core/ratelimit/config.py")

    def defaults_for(fn_name: str) -> dict[str, object]:
        fn = _func_node(tree, fn_name)
        assert fn is not None
        seg = ast.get_source_segment(src, fn) or ""
        keys = [
            "rate_limiting.global.requests",
            "rate_limiting.global.window_seconds",
            "rate_limiting.global.burst",
            "rate_limiting.user.requests",
            "rate_limiting.user.window_seconds",
            "rate_limiting.user.burst",
            "rate_limiting.user.hourly_limit",
            "rate_limiting.user.daily_limit",
            "rate_limiting.ip.requests",
            "rate_limiting.ip.window_seconds",
            "rate_limiting.ip.burst",
            "rate_limiting.ip.hourly_limit",
            "rate_limiting.ip.daily_limit",
        ]
        out = {}
        for k in keys:
            v = _app_config_get_default(seg, k)
            if v is not None:
                out[k] = v
        return out

    g = defaults_for("_build_global_limit")
    assert g["rate_limiting.global.requests"] == 25000
    assert g["rate_limiting.global.window_seconds"] == 60.0
    assert g["rate_limiting.global.burst"] == 5000

    u = defaults_for("_build_user_limit")
    assert u["rate_limiting.user.requests"] == 10000
    assert u["rate_limiting.user.window_seconds"] == 60.0
    assert u["rate_limiting.user.burst"] == 2500
    assert u["rate_limiting.user.hourly_limit"] == 300000
    assert u["rate_limiting.user.daily_limit"] == 5000000

    ip = defaults_for("_build_ip_limit")
    assert ip["rate_limiting.ip.requests"] == 5000
    assert ip["rate_limiting.ip.window_seconds"] == 60.0
    assert ip["rate_limiting.ip.burst"] == 1500
    assert ip["rate_limiting.ip.hourly_limit"] == 150000
    assert ip["rate_limiting.ip.daily_limit"] == 1000000
