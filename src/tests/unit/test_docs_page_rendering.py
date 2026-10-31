"""Docs pages must have one <h1>, and the sidebar must not repeat a page.

Both defects were visible on the live deployment: every page carried the shell
header's <h1> plus the markdown document's own "# Title", and the sidebar
linked /docs/api/configuration twice (once in "Guide", once as the
"Configuration" section's overview).
"""

from src.api.routes.docs.config import DocsConfig
from src.api.routes.docs.navigation import build_sidebar_html
from src.api.routes.docs.renderer import (
    _demote_title_heading,
    markdown_to_html,
    resolve_doc_link,
)

# The subset of served routes the cases below resolve against.
ROUTES = {
    "/",
    "/getting-started",
    "/configuration",
    "/config-api",
    "/reference/system",
    "/admin",
    "/end-user",
    "/deployment",
}


def test_page_heading_is_demoted_so_the_page_has_one_h1():
    html = markdown_to_html(
        "# Get started\n\n## Install\n\nBody text.\n",
        "Getting Started",
        DocsConfig(),
        "/getting-started",
    )
    # The shell header owns the page's only h1.
    assert html.count("<h1") == 0
    # The document's own title stays visible, one level down.
    assert "<h2>Get started</h2>" in html
    # Only the title moves: a later "## Install" is a real section and stays h2.
    assert "<h2>Install</h2>" in html


def test_hash_comment_inside_a_code_fence_is_left_alone():
    source = "# Title\n\n```bash\n# install it\napt-get install thing\n```\n"

    out = _demote_title_heading(source)

    assert out.startswith("## Title")
    assert "# install it" in out
    assert "## install it" not in out


def test_sidebar_links_each_page_once():
    html = build_sidebar_html(DocsConfig(), "/")

    assert html.count('href="/docs/api/configuration"') == 1


def test_repository_relative_links_map_onto_served_routes():
    # Every one of these 404'd on the live deployment: the markdown says where
    # the file lives, the router serves flat paths.
    assert (
        resolve_doc_link("deployment/configuration/config-api.md", ROUTES)
        == "/config-api"
    )
    assert resolve_doc_link("api/system.md", ROUTES) == "/reference/system"
    assert resolve_doc_link("end-user/index.md", ROUTES) == "/end-user"
    assert resolve_doc_link("admin/index.md#access-tokens", ROUTES) == (
        "/admin#access-tokens"
    )
    assert resolve_doc_link("../getting-started.md", ROUTES) == "/getting-started"
    # Off-site and in-page links are left exactly as written.
    assert resolve_doc_link("https://example.com/x", ROUTES) == "https://example.com/x"
    assert resolve_doc_link("#anchor", ROUTES) == "#anchor"
    # A target no route serves becomes plain text rather than a dead link.
    assert resolve_doc_link("deployment/licensing.md", ROUTES) is None


def test_mistune_is_actually_used_not_the_fallback():
    """mistune 3 broke the plugin imports, and the failure was swallowed.

    The result was every page served by the fallback renderer while mistune was
    installed: no <thead>/<tbody>, no language classes, escaped HTML. That is
    what "docs don't render correctly" looked like on the deployment.
    """
    from src.api.routes.docs import renderer

    assert renderer.MISTUNE_AVAILABLE, (
        "mistune must import: a failure here silently downgrades every page"
    )

    html = markdown_to_html(
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n```bash\n# a comment\n```\n",
        "Page",
        DocsConfig(),
        "/",
    )
    assert "<thead>" in html and "<tbody>" in html
    assert '<code class="language-bash">' in html
    assert 'class="code-block"' not in html  # fallback markup


def test_link_rewrite_leaves_code_fences_alone():
    source = "See [config](deployment/configuration/config-api.md).\n\n```md\n[x](missing.md)\n```\n"

    html = markdown_to_html(source, "Page", DocsConfig(), "/")

    assert 'href="/docs/api/config-api"' in html
    # The example link inside the fence is not navigation.
    assert 'href="/docs/api/missing' not in html
