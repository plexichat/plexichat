"""Narrow-viewport docs CSS must wrap long inline code tokens.

At phone widths (~360px) the docs content column is only a few hundred pixels
wide, while ``docs/api/*.md`` prose embeds unbreakable inline-code tokens such
as ``<admin-base>/auth/2fa/regenerate-backup-codes``. Inline ``<code>`` had no
overflow-wrap, so the longest token set the line's min-content width and the
whole page scrolled horizontally. Fenced code already scrolls inside ``<pre>``.

This is a static assertion over the generated stylesheet source: no DOM, no
rendering, no deep diffs.
"""

import re
from pathlib import Path

THEME = Path(__file__).resolve().parents[2] / "api" / "routes" / "docs" / "theme.py"


def _media_block(css: str, max_width: int) -> str:
    """Return the declaration body of ``@media (max-width: Npx)``."""
    marker = f"@media (max-width: {max_width}px)"
    start = css.find(marker)
    assert start != -1, f"{marker} missing from docs theme"
    opening = css.find("{", start)
    assert opening != -1, f"{marker} has no declaration block"
    depth = 0
    for index in range(opening, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[opening + 1 : index]
    raise AssertionError(f"{marker} block is not closed")


def test_narrow_viewports_wrap_inline_code():
    css = THEME.read_text(encoding="utf-8")
    narrow = _media_block(css, 768)

    rule = re.search(r"(?m)^\s*code\s*\{([^}]*)\}", narrow)
    assert rule, "the 768px media query must style inline code"
    assert re.search(r"overflow-wrap:\s*(?:break-word|anywhere)\s*;", rule.group(1)), (
        "inline code must declare overflow-wrap so an unbreakable endpoint path "
        "in prose cannot force the docs page to scroll horizontally on phones"
    )
