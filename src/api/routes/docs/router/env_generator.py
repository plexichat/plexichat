"""
Mixin providing the .env generator and utility page route handlers.
"""

from fastapi import Request
from fastapi.responses import HTMLResponse


class EnvGeneratorMixin:
    async def docs_env_generator(self, request: Request):
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Environment Configuration Generator - Plexichat</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
        .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { color: #333; margin-bottom: 10px; }
        .subtitle { color: #666; margin-bottom: 30px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-weight: bold; margin-bottom: 5px; color: #333; }
        input[type="text"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
        small { color: #666; display: block; margin-top: 5px; }
        .buttons { margin: 20px 0; }
        button { padding: 12px 24px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; margin-right: 10px; }
        .btn-generate { background: #007bff; color: white; }
        .btn-copy { background: #28a745; color: white; }
        button:hover { opacity: 0.9; }
        #copy-status { margin-top: 10px; color: #28a745; font-weight: bold; display: none; }
        textarea { width: 100%; height: 500px; padding: 15px; border: 1px solid #ddd; border-radius: 4px; font-family: 'Courier New', monospace; font-size: 12px; box-sizing: border-box; background: #fafafa; }
        .info-box { background: #e7f3ff; border-left: 4px solid #007bff; padding: 15px; margin: 20px 0; }
        .warning-box { background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Environment Configuration Generator</h1>
        <p class="subtitle">Generate secure .env configuration for Plexichat deployment</p>

        <div class="info-box">
            <strong>Security Note:</strong> All values are generated using cryptographically secure random number generation. Your keys never leave your browser.
        </div>

        <div class="form-group">
            <label for="version">Version to Deploy:</label>
            <input type="text" id="version" value="latest" placeholder="e.g., a.1.0-53">
            <small>Use 'latest' for the most recent version, or specify a version tag like 'a.1.0-53'</small>
        </div>

        <div class="buttons">
            <button onclick="generateEnv()" class="btn-generate">Generate Secure .env</button>
            <button onclick="copyToClipboard()" class="btn-copy">Copy to Clipboard</button>
        </div>

        <div id="copy-status">\u2713 Copied to clipboard!</div>

        <div class="form-group">
            <label for="env-output">Generated .env File:</label>
            <textarea id="env-output" readonly></textarea>
        </div>

        <div class="warning-box">
            <strong>Important:</strong> Save this file as <code>.env.generated</code> in your Plexichat directory. Never commit this file to version control. Keep a backup of your encryption keys.
        </div>

        <h2>Deployment Instructions</h2>
        <ol>
            <li>Generate configuration using the button above</li>
            <li>Copy to clipboard and save as <code>.env.generated</code> on your server</li>
            <li>Run: <code>VERSION=a.1.0-53 docker compose up -d</code></li>
        </ol>
    </div>

    <script>
    function generateSecureHex(length) {
        const array = new Uint8Array(length);
        crypto.getRandomValues(array);
        return Array.from(array, byte => byte.toString(16).padStart(2, '0')).join('');
    }

    function generateEnv() {
        const version = document.getElementById('version').value || 'latest';

        const env = `# Plexichat Docker Environment Configuration
# Generated securely on ${new Date().toISOString()}
# DO NOT commit this file to version control

# Version to deploy (e.g., a.1.0-53)
# Set via: VERSION=a.1.0-53 docker compose up -d
VERSION=${version}

POSTGRES_DBNAME=plexichat
POSTGRES_USER=plexichat
POSTGRES_PASSWORD=${generateSecureHex(24)}
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_SSLMODE=disable

REDIS_PASSWORD=${generateSecureHex(24)}
REDIS_HOST=redis
REDIS_PORT=6379

MINIO_ROOT_USER=plexichat
MINIO_ROOT_PASSWORD=${generateSecureHex(24)}
S3_BUCKET=plexichat-media
S3_REGION=us-east-1
S3_ACCESS_KEY=plexichat
S3_SECRET_KEY=${generateSecureHex(24)}
S3_ENDPOINT=http://minio:9000
S3_PUBLIC_URL=

PLEXICHAT_SYSTEM_KEY=${generateSecureHex(32)}
PLEXICHAT_MESSAGE_KEY=${generateSecureHex(32)}
PLEXICHAT_MEDIA_KEY=${generateSecureHex(32)}

PLEXICHAT_SMTP_PASSWORD=

LOG_LEVEL=INFO
MONITORING_ENABLED=true
MONITORING_METRICS_ENABLED=true
NO_STRICT_CONFIG=false
`;

        document.getElementById('env-output').value = env;
    }

    function copyToClipboard() {
        const envOutput = document.getElementById('env-output');
        envOutput.select();
        document.execCommand('copy');

        const status = document.getElementById('copy-status');
        status.style.display = 'block';
        setTimeout(() => {
            status.style.display = 'none';
        }, 2000);
    }

    window.onload = generateEnv;
    </script>
</body>
</html>"""
        return HTMLResponse(html)

    async def docs_security_logout(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/security-logout.md"),
            "Security Logout",
            "/security-logout",
        )

    async def docs_access_blocked(self, request: Request):
        return await self._serve_page(
            request,
            self._doc_path("end-user/access-blocked.md"),
            "Access Blocked",
            "/access-blocked",
        )
