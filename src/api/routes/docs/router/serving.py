"""
Mixin providing the core _serve_page method for rendering documentation pages.
"""

from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from ..config import _runtime_docs_config, get_docs_config
from ..renderer import markdown_to_html
from ..navigation import (
    build_sidebar_html,
    build_shell_header_html,
    build_footer_html,
)
from ..theme import build_brand_styles


class ServingMixin:
    async def _serve_page(
        self, request: Request, file_path: Path, title: str, current_path: str = ""
    ) -> HTMLResponse:
        conf = _runtime_docs_config(request, get_docs_config())

        try:
            mtime_ns = file_path.stat().st_mtime_ns
        except FileNotFoundError:
            raise HTTPException(404, detail="Page not found")

        source_key = f"{file_path.resolve()}|{mtime_ns}"
        html_key = self._build_html_cache_key(source_key, title, current_path, conf)

        if conf.cache.enabled and conf.cache.cache_html:
            cached_html = self._get_cached_value(
                self._html_cache, html_key, conf.cache.ttl_seconds
            )
            if cached_html is not None:
                return HTMLResponse(cached_html)

        content: Optional[str] = None
        if conf.cache.enabled and conf.cache.cache_markdown:
            content = self._get_cached_value(
                self._docs_cache, source_key, conf.cache.ttl_seconds
            )

        if content is None:
            content = file_path.read_text(encoding="utf-8")
            if conf.cache.enabled and conf.cache.cache_markdown and content:
                self._set_cached_value(
                    self._docs_cache, source_key, content, conf.cache.max_entries
                )

        if not content:
            raise HTTPException(404, detail="Page not found")

        html_content = markdown_to_html(
            content, f"{title} - {conf.title}", conf, current_path
        )
        sidebar_html = build_sidebar_html(conf, current_path)
        footer_html = build_footer_html(conf)
        page_title = title.split(" - ", 1)[0]
        shell_header = build_shell_header_html(
            conf,
            "portal",
            page_title,
            "Guides, route-group overviews, and live schema entry points for the Plexichat backend.",
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - {conf.title}</title>
    <style>{build_brand_styles(conf)}</style>
</head>
<body class="plexi-docs-page">
    <div class="docs-layout">
        {sidebar_html}
        <main class="docs-main">
            <div class="docs-content">
                {shell_header}
                <div class="content-container">{html_content}</div>
                {footer_html}
            </div>
        </main>
    </div>
    <button class="dark-mode-toggle" onclick="toggleDarkMode()" id="darkModeToggle" aria-label="Toggle dark mode">&#x263E;</button>
    <script>
        document.querySelectorAll('.copy-btn').forEach(btn => {{
            btn.addEventListener('click', async () => {{
                const code = document.getElementById(btn.dataset.target).textContent;
                await navigator.clipboard.writeText(code);
                const originalText = btn.textContent;
                btn.textContent = 'Copied';
                setTimeout(() => btn.textContent = originalText, 2000);
            }});
        }});

        function toggleNavCategory(element) {{
            const navList = element.nextElementSibling;
            navList.classList.toggle('collapsed');
            element.classList.toggle('collapsed');
        }}

        function toggleDarkMode() {{
            var theme = document.documentElement.getAttribute('data-theme');
            if (theme === 'dark') {{
                document.documentElement.removeAttribute('data-theme');
                localStorage.setItem('plexi-docs-theme', 'light');
            }} else {{
                document.documentElement.setAttribute('data-theme', 'dark');
                localStorage.setItem('plexi-docs-theme', 'dark');
            }}
        }}

        (function() {{
            var saved = localStorage.getItem('plexi-docs-theme');
            if (saved === 'dark') {{
                document.documentElement.setAttribute('data-theme', 'dark');
            }}
        }})();
    </script>
</body>
</html>"""

        if conf.cache.enabled and conf.cache.cache_html:
            self._set_cached_value(
                self._html_cache, html_key, html, conf.cache.max_entries
            )

        return HTMLResponse(html)
