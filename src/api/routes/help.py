"""
Help routes - Serve user-facing help documentation.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from pathlib import Path

from src.core.security.hardening.paths import safe_join

router = APIRouter(tags=["Help"])
robots_router = APIRouter()

_FALLBACK_ROBOTS_TXT = "User-agent: *\nDisallow: /\n"

# Help pages are static HTML with inline styles and no scripts. Refuse MIME
# sniffing and lock the document down so a future edit cannot introduce a
# script or form sink without an explicit policy change.
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}
_HELP_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


def _resolve_robots_txt() -> Path | None:
    """Locate robots.txt within a trusted static directory.

    The old CWD-based candidate let a process started from a hostile directory
    serve an arbitrary ``static/robots.txt``. Resolve the file through the
    shared confinement helper so the served path can never leave the shipped
    static tree.
    """
    static_dir = Path(__file__).resolve().parent.parent.parent.parent / "static"
    try:
        candidate = Path(safe_join(str(static_dir), "robots.txt"))
    except ValueError:
        return None
    # Allowlisted basename plus resolved-path confinement: the file served is
    # exactly the shipped robots.txt, never a symlink or a sibling with the
    # same prefix. nosniff on the response prevents content-type confusion.
    if candidate.name != "robots.txt" or not candidate.is_file():
        return None
    if not candidate.resolve().is_relative_to(static_dir.resolve()):
        return None
    return candidate


@robots_router.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Serve robots.txt."""
    static_path = _resolve_robots_txt()
    if static_path is None:
        return PlainTextResponse(
            _FALLBACK_ROBOTS_TXT, media_type="text/plain", headers=_NOSNIFF
        )
    return FileResponse(static_path, media_type="text/plain", headers=_NOSNIFF)


def _get_help_style():
    return """
    :root {
        --bg-color: #1a1a2e;
        --text-color: #eaeaea;
        --primary-color: #e94560;
        --card-bg: #16213e;
        --border-color: #0f3460;
    }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        background-color: var(--bg-color);
        color: var(--text-color);
        line-height: 1.6;
        margin: 0;
        padding: 2rem;
        display: flex;
        justify-content: center;
    }
    .container {
        max-width: 800px;
        width: 100%;
    }
    h1 { color: var(--primary-color); border-bottom: 2px solid var(--border-color); padding-bottom: 0.5rem; }
    .card {
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
    }
    .back-link {
        display: inline-block;
        margin-top: 2rem;
        color: var(--primary-color);
        text-decoration: none;
    }
    .back-link:hover { text-decoration: underline; }
    """


@router.get(
    "/security-logout",
    responses={
        200: {
            "description": "HTML help page",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    },
)
async def security_logout_help():
    """Help page explaining security logouts."""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Security Notice - Plexichat Help</title>
        <style>{_get_help_style()}</style>
    </head>
    <body>
        <div class="container">
            <h1>Security Notice: Forced Logout</h1>
            <div class="card">
                <p>You have been logged out of your Plexichat account as a security precaution.</p>
                <p>This typically happens for one of the following reasons:</p>
                <ul>
                    <li>An administrator has invalidated all active sessions due to a system-wide security update.</li>
                    <li>Your account was manually logged out from all devices by an administrator or via your security settings.</li>
                    <li>A security policy change required a session reset.</li>
                </ul>
                <p><strong>What should I do?</strong></p>
                <p>In most cases, you can simply log back in to resume using Plexichat. If you continue to experience issues, please contact the server administrator.</p>
            </div>
            <a href="/" class="back-link">&larr; Return to Plexichat</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(
        content=html,
        headers={**_NOSNIFF, "Content-Security-Policy": _HELP_CSP},
    )


@router.get(
    "/access-blocked",
    responses={
        200: {
            "description": "HTML help page",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    },
)
async def access_blocked_help():
    """Help page explaining IP blocks."""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Access Blocked - Plexichat Help</title>
        <style>{_get_help_style()}</style>
    </head>
    <body>
        <div class="container">
            <h1>Access Blocked</h1>
            <div class="card">
                <p>Your access to this Plexichat server has been restricted.</p>
                <p>This may be due to:</p>
                <ul>
                    <li>Frequent violations of the server's terms of service.</li>
                    <li>Automated detection of abusive behavior from your IP address.</li>
                    <li>A manual block placed by a server administrator.</li>
                </ul>
                <p>If you believe this is an error, please wait for the block to expire or contact the server administration through other channels if available.</p>
            </div>
            <a href="/" class="back-link">&larr; Return to Plexichat</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(
        content=html,
        headers={**_NOSNIFF, "Content-Security-Policy": _HELP_CSP},
    )
