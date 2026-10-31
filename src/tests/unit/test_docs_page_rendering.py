"""Docs pages must have one <h1>, and the sidebar must not repeat a page.

Both defects were visible on the live deployment: every page carried the shell
header's <h1> plus the markdown document's own "# Title", and the sidebar
linked /docs/api/configuration twice (once in "Guide", once as the
"Configuration" section's overview).
"""

from src.api.routes.docs.config import DocsConfig
from src.api.routes.docs.navigation import build_sidebar_html
from src.api.routes.docs.renderer import _demote_title_heading, markdown_to_html


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
