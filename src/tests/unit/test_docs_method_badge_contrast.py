"""HTTP method badges on the Swagger UI docs page must clear WCAG AA.

The CDN Swagger UI stylesheet paints ``.opblock-summary-method`` badges with
white text on light method fills: GET ``#61affe`` (2.31:1), POST ``#49cc90``
(2.03:1) and DELETE ``#f93e3e`` (3.63:1) -- all below the 4.5:1 AA threshold
for their small bold 12px label. That stylesheet only switches to dark ink
under ``html.dark-mode``, but the docs theme toggles ``[data-theme="dark"]``,
so its dark-mode fallback never applies and the badges are low-contrast in
every theme.

The docs theme therefore re-inks the badges through ``--method-badge-ink``,
which reuses existing colors (body ink in light mode, ``--code-bg`` in dark
mode). Assertions are scalar/boolean static checks on the generated CSS plus
an approximate WCAG ratio computed from the resolved token values -- no DOM,
no deep diffs.
"""

from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "api" / "routes" / "docs"
THEME = (DOCS / "theme.py").read_text(encoding="utf-8")

# Swagger UI (swagger-ui-dist@5, the CDN version FastAPI loads by default)
# method-fill colors. Each badge uses the shared `.opblock-summary-method`
# white foreground until something overrides it.
METHOD_FILLS = {
    "GET": "#61affe",
    "POST": "#49cc90",
    "DELETE": "#f93e3e",
}


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = [linear(c) for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def test_method_badge_ink_token_reuses_existing_colors():
    # Light mode aliases body ink; dark mode aliases the near-black code bg.
    assert "--method-badge-ink: var(--text);" in THEME
    assert "--method-badge-ink: var(--code-bg);" in THEME


def test_method_badges_are_inked_with_the_theme_token():
    assert ".plexi-swagger-page .swagger-ui .opblock .opblock-summary-method {" in THEME
    # The override must set the foreground to the token, not a literal color.
    block = THEME.split(
        ".plexi-swagger-page .swagger-ui .opblock .opblock-summary-method {", 1
    )[1].split("}", 1)[0]
    assert "color: var(--method-badge-ink);" in block


def test_dark_theme_method_badge_ink_clears_wcag_aa():
    # In dark mode --code-bg is #000000; the token reuses that exact color.
    dark_ink = "#000000"
    assert "--code-bg: #000000;" in THEME

    for method, fill in METHOD_FILLS.items():
        ratio = _contrast_ratio(dark_ink, fill)
        assert ratio >= 4.5, f"{method} badge {fill} vs ink {dark_ink}: {ratio:.2f}:1"
