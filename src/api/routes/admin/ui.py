"""
Admin UI routes serving HTML templates.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .utils import check_host_restriction, load_admin_template
from src.utils.security import generate_csp_nonce, build_admin_csp_header

router = APIRouter()

@router.get("/", include_in_schema=False)
async def admin_root(request: Request):
    return RedirectResponse(url=request.url_for("admin_login_page"))

@router.get("/login", include_in_schema=False)
async def admin_login_page(request: Request):
    check_host_restriction(request)
    nonce = generate_csp_nonce()
    content = load_admin_template("login.html", csp_nonce=nonce)
    return HTMLResponse(content=content, headers={"Content-Security-Policy": build_admin_csp_header(nonce)})

@router.get("/ui", include_in_schema=False)
async def admin_ui_redirect(request: Request):
    return RedirectResponse(url=request.url_for("admin_dashboard_page"))

@router.get("/ui-dashboard", include_in_schema=False)
async def admin_dashboard_page(request: Request):
    check_host_restriction(request)
    nonce = generate_csp_nonce()
    content = load_admin_template("dashboard.html", csp_nonce=nonce)
    return HTMLResponse(content=content, headers={"Content-Security-Policy": build_admin_csp_header(nonce)})
