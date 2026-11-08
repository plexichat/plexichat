"""Regression tests for the admin dashboard's delegated click handlers."""

import re
from pathlib import Path

DASHBOARD_JS = Path("src/api/templates/admin/dashboard.js")
DASHBOARD_HTML = Path("src/api/templates/admin/dashboard.html")
ADMIN_USERS_API = Path("src/api/routes/admin/users/admin_users.py")
CORE_ADMIN_USERS = Path("src/core/admin/users.py")


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


def test_manage_user_has_no_inline_event_handlers():
    """CSP uses script-src-attr, so inline onerror/onclick is blocked."""
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    assert not re.search(r"\son[a-z]+\s*=", source)
    assert "admin-user-avatar" in source
    assert "avatar.addEventListener('error'" in source


def test_manage_user_exposes_storage_quota_controls():
    source = DASHBOARD_JS.read_text(encoding="utf-8")

    assert "Storage quota" in source
    assert "loadUserStorage()" in source
    assert "/storage`" in source
    assert "saveUserStorage" in source
    assert "clearUserStorage" in source


def test_admin_user_responses_use_the_real_email_column():
    api_source = ADMIN_USERS_API.read_text(encoding="utf-8")
    core_source = CORE_ADMIN_USERS.read_text(encoding="utf-8")

    assert 'email="[Encrypted]"' not in api_source
    assert "u.email_encrypted" in core_source
    assert "_decrypt_email" in core_source
    assert '"[Encrypted]"' not in core_source
