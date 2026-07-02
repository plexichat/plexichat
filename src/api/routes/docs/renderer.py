"""
Markdown to HTML renderer using mistune.

This module handles all markdown rendering with proper syntax highlighting.
"""

import re
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
    from mistune.plugins import (
        plugin_strikethrough,
        plugin_footnotes,
        plugin_table,
        plugin_task_lists,
    )

    MISTUNE_AVAILABLE = True
except ImportError:
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
            "config-redis": "/config-redis",
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


def markdown_to_html(
    markdown_content: str, title: str, conf: DocsConfig, current_path: str = ""
) -> str:
    """Convert markdown to HTML with modern styling using mistune."""
    # Replace dynamic placeholders first (before rendering)
    markdown_content = _replace_dynamic_placeholders(markdown_content, conf)

    if MISTUNE_AVAILABLE:
        assert mistune is not None
        # Use mistune with plugins for better rendering
        renderer = mistune.HTMLRenderer()
        markdown = mistune.Markdown(
            renderer=renderer,
            plugins=[
                plugin_strikethrough,
                plugin_footnotes,
                plugin_table,
                plugin_task_lists,
            ],
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
