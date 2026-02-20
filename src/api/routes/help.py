"""
Help routes - Serve user-facing help documentation.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, FileResponse
import os

router = APIRouter(tags=["Help"])


@router.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Serve robots.txt."""
    static_path = os.path.join(os.getcwd(), "static", "robots.txt")
    return FileResponse(static_path)


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


@router.get("/security-logout")
async def security_logout_help():
    """Help page explaining security logouts."""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Security Notice - PlexiChat Help</title>
        <style>{_get_help_style()}</style>
    </head>
    <body>
        <div class="container">
            <h1>Security Notice: Forced Logout</h1>
            <div class="card">
                <p>You have been logged out of your PlexiChat account as a security precaution.</p>
                <p>This typically happens for one of the following reasons:</p>
                <ul>
                    <li>An administrator has invalidated all active sessions due to a system-wide security update.</li>
                    <li>Your account was manually logged out from all devices by an administrator or via your security settings.</li>
                    <li>A security policy change required a session reset.</li>
                </ul>
                <p><strong>What should I do?</strong></p>
                <p>In most cases, you can simply log back in to resume using PlexiChat. If you continue to experience issues, please contact the server administrator.</p>
            </div>
            <a href="/" class="back-link">&larr; Return to PlexiChat</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/access-blocked")
async def access_blocked_help():
    """Help page explaining IP blocks."""
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Access Blocked - PlexiChat Help</title>
        <style>{_get_help_style()}</style>
    </head>
    <body>
        <div class="container">
            <h1>Access Blocked</h1>
            <div class="card">
                <p>Your access to this PlexiChat server has been restricted.</p>
                <p>This may be due to:</p>
                <ul>
                    <li>Frequent violations of the server's terms of service.</li>
                    <li>Automated detection of abusive behavior from your IP address.</li>
                    <li>A manual block placed by a server administrator.</li>
                </ul>
                <p>If you believe this is an error, please wait for the block to expire or contact the server administration through other channels if available.</p>
            </div>
            <a href="/" class="back-link">&larr; Return to PlexiChat</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
