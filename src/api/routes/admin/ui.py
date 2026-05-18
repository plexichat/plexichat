"""
Admin UI routes serving HTML templates and static assets.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .utils import check_host_restriction, load_admin_template
from src.utils.security import generate_csp_nonce, build_admin_csp_header
from pathlib import Path

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


@router.get("/static/dashboard.css", include_in_schema=False)
async def admin_dashboard_css(request: Request):
    """
    Serve the modularized dashboard CSS file.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()

    # Get the template directory
    template_dir = Path(__file__).parent.parent.parent / "templates" / "admin"
    css_path = template_dir / "dashboard.css"

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


@router.get("/static/dashboard.js", include_in_schema=False)
async def admin_dashboard_js(request: Request):
    """
    Serve the modularized dashboard JavaScript file.
    """
    check_host_restriction(request)
    nonce = generate_csp_nonce()

    # Get the template directory
    template_dir = Path(__file__).parent.parent.parent / "templates" / "admin"
    js_path = template_dir / "dashboard.js"

    if not js_path.exists():
        return HTMLResponse(content="/* JS file not found */", status_code=404)

    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()

    headers = {
        "Content-Type": "application/javascript; charset=utf-8",
        "Content-Security-Policy": build_admin_csp_header(nonce),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0, private",
    }
    return HTMLResponse(content=js_content, headers=headers)
