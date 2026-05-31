"""Default SVG placeholder mixin for the avatars module."""

import hashlib
from typing import Any

from .protocol import AvatarProtocol


class AvatarDefaultsMixin(AvatarProtocol):
    """Mixin generating default SVG placeholder avatars."""

    def generate_default_svg(self, seed: Any, initials: str) -> str:
        """Generate a colorful SVG placeholder avatar based on a stable string seed."""
        # Match frontend colors in ui.js:getAvatarColor
        colors = self._get_config(
            "default_colors",
            ["#6d86d9", "#5ea381", "#c99563", "#5f9ccf", "#8c79c8", "#c27ba2"],
        )

        seed_bytes = str(seed or initials or "user").strip().lower().encode("utf-8")
        color = colors[hashlib.sha256(seed_bytes).digest()[0] % len(colors)]

        return f"""<svg width="128" height="128" viewBox="0 0 128 128" xmlns="http://www.w3.org/2000/svg">
        <rect width="128" height="128" fill="{color}"/>
        <text x="50%" y="50%" font-family="Arial, sans-serif" font-size="48" font-weight="bold" 
              fill="white" text-anchor="middle" dominant-baseline="central">{initials}</text>
    </svg>"""
