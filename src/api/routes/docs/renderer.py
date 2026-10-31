"""
Markdown to HTML renderer using mistune.

This module handles all markdown rendering with proper syntax highlighting.
"""

import re
from typing import List, Optional

from .config import DocsConfig
from .dynamic import (
    get_api_rate_limits,
    get_gateway_intents_docs_data,
    get_permissions_docs_data,
    get_oauth_scopes_docs_data,
    _build_rate_limit_defaults_rows,
    _build_rate_limit_route_rows,
    _build_rate_limit_policy_rows,
    _build_gateway_intent_rows,
    _build_permission_category_rows,
    _build_permission_detail_rows,
    _build_oauth_scope_rows,
    get_app_config,
)

try:
    import mistune

    MISTUNE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercises the fallback renderer only
    MISTUNE_AVAILABLE = False


def _replace_dynamic_placeholders(text: str, conf: DocsConfig) -> str:
    """Replace dynamic placeholders in documentation content."""
    # Replace API base URL placeholders
    text = text.replace("{{BASE_URL}}", conf.base_url)
    text = text.replace("{{API_BASE_URL}}", conf.base_url)

    # Replace WebSocket URL placeholders
    text = text.replace("{{WEBSOCKET_URL}}", conf.websocket_url)
    text = text.replace("{{WS_URL}}", conf.websocket_url)

    if "{{VERSION}}" in text:
        app_config = get_app_config()
        text = text.replace("{{VERSION}}", app_config["version"])

    rate_limit_tokens = (
        "{{RATE_LIMIT_DEFAULT_ROWS}}",
        "{{RATE_LIMIT_ROUTE_ROWS}}",
        "{{RATE_LIMIT_POLICY_ROWS}}",
    )
    if any(token in text for token in rate_limit_tokens):
        limits = get_api_rate_limits()
        text = text.replace(
            "{{RATE_LIMIT_DEFAULT_ROWS}}", _build_rate_limit_defaults_rows(limits)
        )
        text = text.replace(
            "{{RATE_LIMIT_ROUTE_ROWS}}", _build_rate_limit_route_rows(limits)
        )
        text = text.replace(
            "{{RATE_LIMIT_POLICY_ROWS}}", _build_rate_limit_policy_rows(limits)
        )

    gateway_intent_tokens = (
        "{{GATEWAY_DEFAULT_INTENTS}}",
        "{{GATEWAY_ALL_INTENTS}}",
        "{{GATEWAY_PRIVILEGED_INTENTS}}",
        "{{GATEWAY_INTENT_ROWS}}",
    )
    if any(token in text for token in gateway_intent_tokens):
        intents = get_gateway_intents_docs_data()
        text = text.replace(
            "{{GATEWAY_DEFAULT_INTENTS}}", str(intents.get("default_value", 0))
        )
        text = text.replace("{{GATEWAY_ALL_INTENTS}}", str(intents.get("all_value", 0)))
        text = text.replace(
            "{{GATEWAY_PRIVILEGED_INTENTS}}",
            str(intents.get("privileged_value", 0)),
        )
        text = text.replace(
            "{{GATEWAY_INTENT_ROWS}}", _build_gateway_intent_rows(intents)
        )

    permission_tokens = (
        "{{PERMISSION_CATEGORY_COUNT}}",
        "{{PERMISSION_TOTAL_COUNT}}",
        "{{PERMISSION_CATEGORY_ROWS}}",
        "{{PERMISSION_DETAIL_ROWS}}",
    )
    if any(token in text for token in permission_tokens):
        permissions = get_permissions_docs_data()
        text = text.replace(
            "{{PERMISSION_CATEGORY_COUNT}}",
            str(permissions.get("category_count", 0)),
        )
        text = text.replace(
            "{{PERMISSION_TOTAL_COUNT}}",
            str(permissions.get("permission_count", 0)),
        )
        text = text.replace(
            "{{PERMISSION_CATEGORY_ROWS}}",
            _build_permission_category_rows(permissions),
        )
        text = text.replace(
            "{{PERMISSION_DETAIL_ROWS}}", _build_permission_detail_rows(permissions)
        )

    oauth_scope_tokens = (
        "{{OAUTH_SCOPE_COUNT}}",
        "{{OAUTH_PRIVILEGED_SCOPE_COUNT}}",
        "{{OAUTH_BOT_SCOPE_COUNT}}",
        "{{OAUTH_SCOPE_ROWS}}",
    )
    if any(token in text for token in oauth_scope_tokens):
        oauth_scopes = get_oauth_scopes_docs_data()
        text = text.replace(
            "{{OAUTH_SCOPE_COUNT}}", str(oauth_scopes.get("scope_count", 0))
        )
        text = text.replace(
            "{{OAUTH_PRIVILEGED_SCOPE_COUNT}}",
            str(oauth_scopes.get("privileged_count", 0)),
        )
        text = text.replace(
            "{{OAUTH_BOT_SCOPE_COUNT}}",
            str(oauth_scopes.get("bot_required_count", 0)),
        )
        text = text.replace(
            "{{OAUTH_SCOPE_ROWS}}", _build_oauth_scope_rows(oauth_scopes)
        )

    return text


def _convert_markdown_links(text: str, conf: DocsConfig, current_path: str = "") -> str:
    """Convert markdown links to proper HTML links."""
    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"

    def replace_link(match):
        link_text = match.group(1)
        link_url = match.group(2)

        if link_url.startswith((f"{conf.path}/", f"{conf.path}#")):
            # Already resolved by _rewrite_doc_links, which runs first; adding
            # the prefix again produced /docs/api/docs/api/... links.
            return f'<a href="{link_url}">{link_text}</a>'

        if link_url.startswith(("http://", "https://", "#", "mailto:")):
            return f'<a href="{link_url}">{link_text}</a>'

        normalized_link = link_url
        while normalized_link.startswith("../"):
            normalized_link = normalized_link[3:]
        if normalized_link.startswith("./"):
            normalized_link = normalized_link[2:]
        if normalized_link.endswith(".md"):
            normalized_link = normalized_link[:-3]

        if link_url.startswith("/"):
            return f'<a href="{conf.path}{link_url}">{link_text}</a>'

        path_mappings = {
            "getting-started": "/getting-started",
            "configuration": "/configuration",
            "default-config": "/default-config",
            "config-authentication": "/config-authentication",
            "config-database": "/config-database",
            "config-valkey": "/config-valkey",
            "config-media": "/config-media",
            "config-voice": "/config-voice",
            "config-websocket": "/config-websocket",
            "config-search": "/config-search",
            "config-rate-limiting": "/config-rate-limiting",
            "config-api": "/config-api",
            "deployment": "/deployment",
            "features": "/features",
            "permissions": "/permissions",
            "security": "/security",
            "performance": "/performance",
            "admin-access-tokens": "/admin-access-tokens",
            "oauth-scopes": "/oauth-scopes",
            "rate-limits": "/rate-limits",
            "errors": "/errors",
            "data-types": "/data-types",
            "security-logout": "/security-logout",
            "access-blocked": "/access-blocked",
            "api/index": "/reference",
            "websocket/index": "/websocket",
            "end-user/getting-started": "/end-user/getting-started",
        }

        if normalized_link in path_mappings:
            link_url = path_mappings[normalized_link]
        elif normalized_link.startswith("api/"):
            link_url = f"/reference/{normalized_link[4:]}"
        elif normalized_link.startswith("websocket/"):
            link_url = f"/websocket/{normalized_link[10:]}"
        elif current_path == "/reference" or current_path.startswith("/reference/"):
            link_url = f"/reference/{normalized_link.lstrip('/')}"
        elif current_path == "/websocket" or current_path.startswith("/websocket/"):
            link_url = f"/websocket/{normalized_link.lstrip('/')}"
        elif current_path == "/deployment" or current_path.startswith("/deployment/"):
            link_url = f"/deployment/{normalized_link.lstrip('/')}"
        else:
            link_url = f"/{normalized_link.lstrip('/')}"

        return f'<a href="{conf.path}{link_url}">{link_text}</a>'

    return re.sub(link_pattern, replace_link, text)


def _demote_title_heading(text: str) -> str:
    """Turn the document's own `# Title` into `## Title`, outside code fences.

    Every page opens with an `# ...` heading, and the page shell already emits
    the page title as the single <h1>. Rendering both put two <h1> elements on
    every docs page, which flattens the document outline that screen readers
    and "jump to heading" lists depend on.

    Only the first top-level heading is demoted -- later `#` lines are real
    sections of a split document -- and fence state is tracked, so a `#`
    comment inside a shell snippet stays exactly as written. Replacing on the
    raw markdown (rather than patching the HTML) keeps this true for both the
    mistune path and the fallback renderer.
    """
    lines = text.split("\n")
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("# "):
            lines[index] = line.replace("# ", "## ", 1)
            break
    return "\n".join(lines)


LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _known_doc_routes() -> set[str]:
    """Paths the docs router actually serves, or empty when unavailable."""
    try:
        from .router import router as docs_router
    except Exception:  # the renderer must stay usable without the router
        return set()
    return {
        route.path
        for route in getattr(docs_router, "routes", [])
        if getattr(route, "path", "").startswith("/")
    }


def resolve_doc_link(
    link_url: str,
    known_routes: Optional[set[str]] = None,
    source_dir: str = "",
) -> Optional[str]:
    """Map a repository-relative markdown link onto a served docs route.

    The pages are written as markdown files in the repository, so they link to
    each other as `[x](deployment/configuration/config-api.md)`. The served
    routes are flat (`/config-api`), and the link was rendered verbatim: 19 of
    the 53 sidebar links 404'd, and every "see also" in the prose with them.

    Returns the path to link to (without the docs prefix), or None when no
    served route matches -- plain text reads better than a dead link.
    """
    if not link_url:
        return None
    if link_url.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return link_url

    path, _, anchor = link_url.partition("#")
    path = path.split("?", 1)[0].strip()
    if not path:
        return f"#{anchor}" if anchor else None

    # Relative prefixes from a repository path, and the .md the file carries.
    while path.startswith("./") or path.startswith("../") or path.startswith("/"):
        path = path[2:] if path.startswith(("./", "../")) else path[1:]
    if path.endswith(".md"):
        path = path[:-3]
    path = path.strip("/")
    if not path:
        return "/"

    routes = _known_doc_routes() if known_routes is None else known_routes
    segments = path.split("/")
    candidates: List[str] = []
    # A sibling link ("security.md" from docs/admin/index.md) is relative to
    # the page's own directory, which only the caller knows. Without this the
    # target had no served route and the link was dropped to plain text --
    # every cross-reference inside a directory lost its link silently.
    if source_dir and "/" not in path:
        candidates.append(f"/{source_dir.strip('/')}/{path}")
    # The configuration pages are served flat, whatever directory they live in.
    if path.startswith("deployment/configuration/"):
        candidates.append("/" + segments[-1])
    # A directory's own landing page is that directory's route.
    if segments[-1] == "index":
        directory = "/".join(segments[:-1])
        candidates.append("/" + directory if directory else "/")
    if path.startswith("api/"):
        candidates.append("/reference/" + path[4:])
    if path.startswith("websocket/"):
        candidates.append("/websocket/" + path[10:])
    candidates.append("/" + path)
    candidates.append("/" + segments[-1])

    for candidate in candidates:
        if candidate in routes:
            return candidate + (f"#{anchor}" if anchor else "")
    return None


def _rewrite_doc_links(text: str, conf: DocsConfig, source_dir: str = "") -> str:
    """Point every in-page link at a route the docs server serves.

    Fenced code is left alone: `[x](y)` inside a snippet is example text, not
    navigation. The *URL* is rewritten and the markdown link is kept --
    injecting an anchor here would be escaped by mistune (it renders the page
    as text, not trusted HTML), which is how the first attempt turned every
    cross-reference into literal `&lt;a href=...` on the page.
    """
    lines = text.split("\n")
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or "](" not in line:
            continue

        def replace(match: "re.Match[str]") -> str:
            label, url = match.group(1), match.group(2)
            resolved = resolve_doc_link(url, None, source_dir)
            if resolved is None:
                # Nothing serves this target: keep the words, drop the link.
                return label
            if resolved.startswith(("http://", "https://", "mailto:", "tel:", "#")):
                return f"[{label}]({resolved})"
            return f"[{label}]({conf.path}{resolved})"

        lines[index] = LINK_RE.sub(replace, line)
    return "\n".join(lines)


def markdown_to_html(
    markdown_content: str,
    title: str,
    conf: DocsConfig,
    current_path: str = "",
    source_dir: str = "",
) -> str:
    """Convert markdown to HTML with modern styling using mistune."""
    # Replace dynamic placeholders first (before rendering)
    markdown_content = _replace_dynamic_placeholders(markdown_content, conf)
    markdown_content = _demote_title_heading(markdown_content)
    markdown_content = _rewrite_doc_links(markdown_content, conf, source_dir)

    if MISTUNE_AVAILABLE:
        assert mistune is not None
        # Plugins by documented *name*, not imported callables.
        #
        # This module used to `from mistune.plugins import
        # plugin_strikethrough, ...`, which no longer exists in mistune 3.x.
        # The ImportError was caught by the same handler as "mistune is not
        # installed", so MISTUNE_AVAILABLE was False on every deployment that
        # shipped mistune 3 -- and every page rendered through the crude
        # fallback below (no thead/tbody, no language classes, escaped HTML)
        # while looking like a styling problem. create_markdown accepts these
        # string names and raises on an unknown one, so this cannot fail
        # silently again.
        markdown = mistune.create_markdown(
            escape=False,  # pages embed generated HTML rows
            plugins=["strikethrough", "footnotes", "table", "task_lists"],
        )
        html_content = markdown(markdown_content)
    else:
        # Fallback to basic rendering if mistune is not available
        import html as html_module

        content = html_module.escape(markdown_content)
        content = _convert_markdown_links(content, conf, current_path)

        lines = content.split("\n")
        html_lines = []
        in_code_block = False
        in_table = False
        in_unordered_list = False
        in_ordered_list = False
        table_row_index = 0
        code_block_id = 0

        def close_lists():
            nonlocal in_unordered_list, in_ordered_list
            if in_unordered_list:
                html_lines.append("</ul>")
                in_unordered_list = False
            if in_ordered_list:
                html_lines.append("</ol>")
                in_ordered_list = False

        def format_inline(text: str) -> str:
            text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
            text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
            return text

        for line in lines:
            if line.startswith("```"):
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                if not in_code_block:
                    code_lang = line[3:].strip() or "text"
                    code_block_id += 1
                    html_lines.append(
                        f'<div class="code-block"><button class="copy-btn" data-target="code-{code_block_id}">Copy</button><pre><code id="code-{code_block_id}" class="language-{code_lang}">'
                    )
                    in_code_block = True
                else:
                    html_lines.append("</code></pre></div>")
                    in_code_block = False
                continue

            if in_code_block:
                html_lines.append(line)
                continue

            if line.startswith("### "):
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                html_lines.append(f"<h3>{format_inline(line[4:])}</h3>")
            elif line.startswith("## "):
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                html_lines.append(f"<h2>{format_inline(line[3:])}</h2>")
            elif line.startswith("# "):
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                html_lines.append(f"<h1>{format_inline(line[2:])}</h1>")
            elif line.startswith("|") and line.endswith("|"):
                close_lists()
                cells = line.split("|")[1:-1]
                if all(c.strip().startswith("-") for c in cells):
                    continue
                if not in_table:
                    html_lines.append('<div class="table-wrapper"><table>')
                    in_table = True
                    table_row_index = 0
                cell_tag = "th" if table_row_index == 0 else "td"
                html_lines.append(
                    f"<tr>{''.join(f'<{cell_tag}>{format_inline(c.strip())}</{cell_tag}>' for c in cells)}</tr>"
                )
                table_row_index += 1
            elif line.startswith("- "):
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                if in_ordered_list:
                    html_lines.append("</ol>")
                    in_ordered_list = False
                if not in_unordered_list:
                    html_lines.append("<ul>")
                    in_unordered_list = True
                html_lines.append(f"<li>{format_inline(line[2:])}</li>")
            elif re.match(r"^[0-9]+\. ", line):
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                if in_unordered_list:
                    html_lines.append("</ul>")
                    in_unordered_list = False
                if not in_ordered_list:
                    html_lines.append("<ol>")
                    in_ordered_list = True
                ordered_item = re.sub(r"^[0-9]+\. ", "", line)
                html_lines.append(f"<li>{format_inline(ordered_item)}</li>")
            elif line.startswith("**Note:**") or line.startswith("**Important:**"):
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                html_lines.append(f'<div class="note">{format_inline(line)}</div>')
            elif line.strip():
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                html_lines.append(f"<p>{format_inline(line)}</p>")
            else:
                close_lists()
                if in_table:
                    html_lines.append("</table></div>")
                    in_table = False
                    table_row_index = 0
                html_lines.append("")

        close_lists()
        if in_table:
            html_lines.append("</table></div>")

        html_content = "\n".join(html_lines)

    return html_content
