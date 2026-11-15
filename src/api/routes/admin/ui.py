"""
Admin UI routes serving HTML templates and static assets.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from .utils import check_host_restriction, load_admin_template
from src.utils.security import generate_csp_nonce, build_admin_csp_header
from pathlib import Path

from src.core.security.hardening import safe_join

_ADMIN_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "templates" / "admin"


def _admin_static_path(*parts: str) -> Path:
    """Resolve a bundled admin asset, refusing anything outside its directory."""
    return Path(safe_join(str(_ADMIN_TEMPLATE_DIR), *parts))


router = APIRouter()


def _security_headers(nonce: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": build_admin_csp_header(nonce),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
        "Pragma": "no-cache",
        "Expires": "0",
    }


@router.get("/", include_in_schema=False)
async def admin_root(request: Request):
    """
    Redirect the root administrator URL to the login page.
    """
    # Use relative path to avoid generating http:// URLs behind a
    # TLS-terminating reverse proxy (which breaks sessionStorage).
    return RedirectResponse(url="/api/v1/admin/login")


@router.get("/login", include_in_schema=False)
async def admin_login_page(request: Request):
    """
    Serve the administrator login HTML page.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()
    content = load_admin_template("login.html", csp_nonce=nonce)
    return HTMLResponse(content=content, headers=_security_headers(nonce))


@router.get("/ui", include_in_schema=False)
async def admin_ui_redirect(request: Request):
    """
    Redirect the generic UI path to the dashboard.
    """
    # Use relative path to avoid generating http:// URLs behind a
    # TLS-terminating reverse proxy (which breaks sessionStorage).
    return RedirectResponse(url="/api/v1/admin/ui-dashboard")


@router.get("/ui-dashboard", include_in_schema=False)
async def admin_dashboard_page(request: Request):
    """
    Serve the administrator dashboard HTML page.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()
    content = load_admin_template("dashboard.html", csp_nonce=nonce)
    return HTMLResponse(content=content, headers=_security_headers(nonce))


@router.get("/ui-migrations", include_in_schema=False)
async def admin_migrations_page(request: Request):
    """
    Serve the administrator migrations HTML page.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()
    content = load_admin_template("migrations.html", csp_nonce=nonce)
    return HTMLResponse(content=content, headers=_security_headers(nonce))


@router.get("/static/favicon.svg", include_in_schema=False)
async def admin_favicon(request: Request):
    """Serve the Plexichat icon used by the admin panel."""
    check_host_restriction(request)
    icon_path = _admin_static_path("favicon.svg")
    if not icon_path.exists():
        return HTMLResponse(content="<!-- icon not found -->", status_code=404)
    with open(icon_path, "rb") as f:
        icon_content = f.read()
    return Response(
        content=icon_content,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/static/chart.umd.min.js", include_in_schema=False)
async def admin_chart_js(request: Request):
    """
    Serve the vendored Chart.js bundle.

    The dashboard used to load Chart.js from a public CDN. That made the admin
    panel depend on a third-party origin (and its SRI hash drifted), so the
    bundle is now shipped with the server and served from this origin.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()

    js_path = _admin_static_path("vendor", "chart.umd.min.js")
    if not js_path.exists():
        return HTMLResponse(content="/* JS file not found */", status_code=404)

    with open(js_path, "rb") as f:
        js_content = f.read()

    headers = {
        "Content-Type": "application/javascript; charset=utf-8",
        "Content-Security-Policy": build_admin_csp_header(nonce),
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    return Response(content=js_content, headers=headers)


@router.get("/static/dashboard.css", include_in_schema=False)
async def admin_dashboard_css(request: Request):
    """
    Serve the modularized dashboard CSS file.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()

    css_path = _admin_static_path("dashboard.css")

    if not css_path.exists():
        return HTMLResponse(content="/* CSS file not found */", status_code=404)

    with open(css_path, "r", encoding="utf-8") as f:
        css_content = f.read()

    headers = {
        "Content-Type": "text/css; charset=utf-8",
        "Content-Security-Policy": build_admin_csp_header(nonce),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
    }
    return HTMLResponse(content=css_content, headers=headers)


_ADMIN_DASHBOARD_JS_CACHE = {"signature": None, "content": ""}

# A concatenated dashboard is ~150 KB today; cap the read so a runaway part
# cannot turn every request into a multi-megabyte allocation.
_MAX_ADMIN_DASHBOARD_JS = 2 * 1024 * 1024


def _admin_dashboard_js() -> str:
    """Concatenate the ordered dashboard parts, caching by (name, mtime)."""
    parts_dir = _admin_static_path("dashboard")
    parts = sorted(parts_dir.glob("*.js"))
    signature = tuple((p.name, p.stat().st_mtime_ns) for p in parts)
    if _ADMIN_DASHBOARD_JS_CACHE["signature"] == signature:
        return _ADMIN_DASHBOARD_JS_CACHE["content"]

    chunks: list[str] = []
    total = 0
    for part in parts:
        text = part.read_text(encoding="utf-8")
        total += len(text) + 1
        if total > _MAX_ADMIN_DASHBOARD_JS:
            # Fail closed: a truncated script would throw at parse time, so
            # surface the problem as a 500 instead of shipping a broken page.
            raise HTTPException(status_code=500, detail="dashboard bundle too large")
        chunks.append(text)
    content = "\n".join(chunks)
    _ADMIN_DASHBOARD_JS_CACHE["signature"] = signature
    _ADMIN_DASHBOARD_JS_CACHE["content"] = content
    return content


@router.get("/static/dashboard.js", include_in_schema=False)
async def admin_dashboard_js(request: Request):
    """
    Serve the modularized dashboard JavaScript file.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()

    # The dashboard script is split into ordered parts under dashboard/ so no
    # single file grows past a reviewable size. They are concatenated in name
    # order, preserving classic-script global semantics. The concatenated body
    # is cached in-process and only re-read when a part's mtime changes.
    parts_dir = _admin_static_path("dashboard")
    if parts_dir.is_dir():
        js_content = _admin_dashboard_js()
    else:
        js_path = _admin_static_path("dashboard.js")
        if not js_path.exists():
            return HTMLResponse(content="/* JS file not found */", status_code=404)
        with open(js_path, "r", encoding="utf-8") as f:
            js_content = f.read()

    headers = {
        "Content-Type": "application/javascript; charset=utf-8",
        "Content-Security-Policy": build_admin_csp_header(nonce),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
        "X-Content-Type-Options": "nosniff",
    }
    return HTMLResponse(content=js_content, headers=headers)
