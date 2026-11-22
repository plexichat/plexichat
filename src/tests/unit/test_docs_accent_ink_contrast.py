"""Accent-filled docs controls need legible ink in both themes (WCAG AA).

Every control painted with ``--accent`` -- the active/hovered sidebar link,
the surface badge, hovered surface/related links, the copy button, the
accented runtime pill, the dark-mode toggle and the Swagger authorize/execute
buttons -- used literal ``color: #fff``. That is fine for the light accent
(#6366f1, 4.47:1) but fails badly against the dark theme's #818cf8, where
white ink is only 2.98:1, under the 4.5:1 AA minimum for their small labels.

The theme now routes all of them through ``--accent-ink``: white in light
mode, the near-black page background (``--bg``) in dark mode, which reaches
6.42:1 against #818cf8. Both values reuse colors already in the palette.

Assertions are scalar/boolean static checks on the generated CSS plus an
approximate WCAG ratio computed from the resolved token values -- no DOM.
"""

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "api" / "routes" / "docs"
THEME = (DOCS / "theme.py").read_text(encoding="utf-8")

LIGHT = THEME.split(":root {", 1)[1].split("}", 1)[0]
DARK = THEME.split('[data-theme="dark"] {', 1)[1].split("}", 1)[0]


def _token(block: str, name: str) -> str:
    match = re.search(rf"--{name}:\s*([^;]+);", block)
    assert match, f"--{name} missing from theme block"
    return match.group(1).strip()


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


def test_accent_ink_token_is_defined_for_both_themes():
    # Light keeps the existing white-on-accent look; dark reuses the page bg.
    assert _token(LIGHT, "accent-ink") == "#ffffff"
    assert _token(DARK, "accent-ink") == "var(--bg)"


def test_accent_filled_controls_all_use_the_token():
    # Literal white ink on an accent fill is the defect; none may remain.
    assert "color: #fff;" not in THEME
    # Every previously-white control now uses the token (dark-mode toggle,
    # nav hover/active, badge, surface/related links, copy button, runtime
    # pill, Swagger authorize/execute).
    assert THEME.count("color: var(--accent-ink);") == 9
    # The active sidebar link -- the most prominent instance -- is included.
    active = THEME.split(".nav-list a.active {", 1)[1].split("}", 1)[0]
    assert "background: var(--accent);" in active
    assert "color: var(--accent-ink);" in active


def test_dark_accent_ink_clears_wcag_aa():
    dark_fill = _token(DARK, "accent")  # #818cf8
    dark_ink_source = _token(DARK, "accent-ink")  # var(--bg)
    assert dark_ink_source == "var(--bg)"
    dark_ink = _token(DARK, "bg")  # #0b0f19

    # The pre-fix value (white) was below AA; this documents the defect.
    assert _contrast_ratio("#ffffff", dark_fill) < 4.5
    # The near-black token resolves to a clearly passing ratio in dark mode.
    ratio = _contrast_ratio(dark_ink, dark_fill)
    assert ratio >= 4.5, f"{dark_ink} ink on {dark_fill} accent: {ratio:.2f}:1"
