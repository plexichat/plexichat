"""Cold docs renders reuse the mistune renderer and the served-route set.

Both were rebuilt repeatedly inside a single render: mistune was constructed
per page, and the full router route set was re-imported for every markdown
link. Page content and link resolution are unchanged.
"""

from src.api.routes.docs import renderer
from src.api.routes.docs.config import DocsConfig


def test_mistune_renderer_is_built_once(monkeypatch):
    calls = []
    real = renderer.mistune.create_markdown

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(renderer, "_markdown_renderer", None)
    monkeypatch.setattr(renderer.mistune, "create_markdown", counting)

    for _ in range(3):
        renderer.markdown_to_html("| a | b |\n|---|---|\n| 1 | 2 |\n", "T", DocsConfig())

    assert len(calls) == 1


def test_known_routes_are_memoized(monkeypatch):
    monkeypatch.setattr(renderer, "_known_routes_cache", None)
    first = renderer._known_doc_routes()
    second = renderer._known_doc_routes()

    assert first == second
    assert first is second


def test_link_rewrite_still_resolves_many_links():
    src = (
        "[config](deployment/configuration/config-api.md) "
        "[database](deployment/configuration/config-database.md) "
        "[started](getting-started.md)\n"
    )
    html = renderer.markdown_to_html(src, "Page", DocsConfig(), "/", "deployment")

    assert 'href="/docs/api/config-api"' in html
    assert 'href="/docs/api/config-database"' in html
