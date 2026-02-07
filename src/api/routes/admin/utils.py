"""
Shared utilities for Admin API routes.
"""

from fastapi import HTTPException, Request, status
from typing import Optional, Any
from pathlib import Path
import utils.config as config
import utils.logger as logger
import src.api as api

def check_host_restriction(request: Request) -> None:
    """Check if client IP is allowed to access admin UI."""
    is_selftest = request.scope.get("state", {}).get("is_selftest", False)
    if not is_selftest:
        is_selftest = getattr(request.state, "is_selftest", False)
    
    if not is_selftest:
        internal_secret = api.get_internal_secret()
        provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
        is_selftest = internal_secret and provided_secret == internal_secret

    if is_selftest:
        return

    admin_config = config.get("admin_ui", {})
    if not admin_config.get("enabled", False):
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Not found"}})

    from src.utils.net import get_client_ip
    client_ip = get_client_ip(request) or "unknown"
    
    localhost_variants = ["127.0.0.1", "localhost", "::1"]
    if client_ip in localhost_variants:
        direct_ip = request.client.host if request.client else "unknown"
        api_config = config.get("api", {})
        trusted_proxies = api_config.get("trusted_proxies", [])
        if direct_ip not in localhost_variants and direct_ip not in trusted_proxies:
            logger.warning(f"CRITICAL: Attempted admin access spoofing from {direct_ip}")
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})

    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        allowed_hosts = host_restriction.get("allowed_hosts", ["127.0.0.1", "localhost", "::1"])
        from src.core import admin
        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})

def get_admin_from_token(request: Request) -> int:
    """Get admin ID from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid token"}})

    token = auth_header[7:]
    
    # Internal bypass check
    is_selftest = request.scope.get("state", {}).get("is_selftest", False) or getattr(request.state, "is_selftest", False)
    if not is_selftest:
        internal_secret = api.get_internal_secret()
        is_selftest = internal_secret and request.headers.get("X-Plexichat-Internal-Secret") == internal_secret

    user = request.scope.get("state", {}).get("user") or getattr(request.state, "user", None)
    if is_selftest and user:
        from src.core.auth.permissions import has_permission
        if has_permission(user.permissions, "admin.*") or has_permission(user.permissions, "*"):
            return user.user_id

    from src.core import admin
    admin_id = admin.validate_session(token)
    if not admin_id:
        raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid or expired token"}})
    return admin_id

def load_admin_template(template_name: str, csp_nonce: Optional[str] = None) -> str:
    """Load an admin UI template."""
    allowed_templates = ["login.html", "dashboard.html"]
    if template_name not in allowed_templates:
        return "<h1>Access Denied</h1>"

    template_dir = Path(__file__).parent.parent.parent / "templates" / "admin"
    template_path = (template_dir / template_name).resolve()

    if not str(template_path).startswith(str(template_dir.resolve())) or not template_path.exists():
        return "<h1>Template Error</h1>"

    try:
        content = template_path.read_text(encoding="utf-8")
        if csp_nonce:
            import re
            content = content.replace("{{ csp_nonce }}", csp_nonce).replace("{{ nonce }}", csp_nonce)
            def script_repl(match):
                tag = match.group(0)
                return tag if 'nonce=' in tag else tag.replace('<script', f'<script nonce="{csp_nonce}"')
            content = re.sub(r'<script[^>]*>', script_repl, content)
        return content
    except Exception as e:
        logger.error(f"Error reading template {template_name}: {e}")
        return f"<h1>Template Error: {e}</h1>"
