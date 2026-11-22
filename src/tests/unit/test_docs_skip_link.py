"""Docs surfaces must expose a keyboard skip link to main content.

The sidebar carries 100+ links before ``<main>`` on every docs page, so without
a skip link a keyboard user has to tab through the whole navigation on each
page load (WCAG 2.4.1 Bypass Blocks). The client shell already ships this
pattern (``<a class="skip-link" href="#main-content">``); the backend-rendered
docs pages were missing both the link and its anchor target.

Assertions are scalar/boolean static checks on the markup/CSS builders -- the
skip link is a literal string injected into each rendered page, so these guard
the exact contract without a DOM deep-diff.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "api" / "routes" / "docs"

SKIP_LINK = '<a class="skip-link" href="#main-content">Skip to content</a>'
MAIN_TARGET = '<main class="docs-main" id="main-content">'


def _read(relative: str) -> str:
    return (DOCS / relative).read_text(encoding="utf-8")


def test_narrative_docs_page_has_skip_link_and_anchor_target():
    html = _read("router/serving.py")

    assert SKIP_LINK in html
    assert MAIN_TARGET in html


def test_swagger_and_redoc_pages_have_skip_link_and_anchor_target():
    html = _read("openapi.py")

    # One anchor and one target per surface (Swagger UI and ReDoc).
    assert html.count(SKIP_LINK) == 2
    assert html.count(MAIN_TARGET) == 2


def test_theme_reveals_skip_link_on_keyboard_focus():
    css = _read("theme.py")

    assert ".skip-link {" in css
    assert ".skip-link:focus," in css
    assert "transform: translateY(-200%)" in css
    assert "transform: translateY(0)" in css
