"""Regression tests for the admin dashboard's delegated click handlers."""

import re
from pathlib import Path

DASHBOARD_JS = Path("src/api/templates/admin/dashboard.js")
DASHBOARD_HTML = Path("src/api/templates/admin/dashboard.html")


def _handlers(source: str) -> dict[str, str]:
    return dict(re.findall(r"\['([A-Za-z0-9_]+)',\s*([A-Za-z_$][\w$]*)\]", source))


def _is_defined(source: str, name: str) -> bool:
    escaped = re.escape(name)
    return bool(
        re.search(rf"(?:async\s+)?function\s+{escaped}\b", source)
        or re.search(rf"(?:const|let|var)\s+{escaped}\b", source)
    )


def test_every_delegated_handler_is_defined():
    """A missing handler aborts the whole dashboard at map construction."""
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    missing = sorted(
        action
        for action, function_name in _handlers(source).items()
        if not _is_defined(source, function_name)
    )

    assert not missing, f"data-click handlers without definitions: {missing}"


def test_every_template_action_is_in_the_handler_map():
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    template = DASHBOARD_HTML.read_text(encoding="utf-8")
    actions = set(re.findall(r'data-click="([A-Za-z0-9_]+)"', template))

    assert not sorted(actions - set(_handlers(source)))
