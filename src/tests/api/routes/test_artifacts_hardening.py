"""Hardening and compatibility tests for artifact routes package."""

from __future__ import annotations


from src.api.routes import artifacts as pkg
from src.api.routes.artifacts import (
    _convert_upload_impl,
    _export_artifact_impl,
    _get_manager,
    _list_artifact_ops_impl,
    _parse_optional_id,
    _require_server_permission,
    router,
    safe_download_filename,
    sanitize_rich_html,
    validate_export_format,
)
from src.api.routes.artifacts_helpers import _get_manager as _helpers_get_manager
from src.api.routes.artifacts_export import sanitize_rich_html as _export_sanitize


class TestCompatSurface:
    def test_package_exports(self) -> None:
        assert pkg.router is router
        assert router.prefix == "/artifacts"
        assert callable(_export_artifact_impl)
        assert callable(_convert_upload_impl)
        assert callable(_list_artifact_ops_impl)
        assert callable(_get_manager)
        assert callable(_require_server_permission)
        assert callable(sanitize_rich_html)
        assert _get_manager is _helpers_get_manager
        assert sanitize_rich_html is _export_sanitize
        assert hasattr(pkg, "_artifact_response")
        assert hasattr(pkg, "_authorize_scope")


class TestIdValidation:
    def test_rejects_malformed_optional_id(self) -> None:
        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _parse_optional_id("abc", "conversation ID")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException):
            _parse_optional_id("-1", "conversation ID")
        with pytest.raises(HTTPException):
            _parse_optional_id("0", "conversation ID")
        with pytest.raises(HTTPException):
            _parse_optional_id("1" * 80, "conversation ID")
        assert _parse_optional_id(None, "x") is None
        assert _parse_optional_id("123", "x") == 123


class TestExportHardening:
    def test_rejects_unknown_format(self) -> None:
        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            validate_export_format("../../etc/passwd")
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException):
            validate_export_format("exe")

    def test_safe_filename_strips_traversal(self) -> None:
        ascii_name, utf8_name = safe_download_filename(
            "../../etc/passwd\r\nX: y", "txt"
        )
        assert ".." not in ascii_name
        assert "/" not in ascii_name
        assert "\\" not in ascii_name
        assert "\r" not in ascii_name and "\n" not in ascii_name
        assert ascii_name.endswith(".txt")

    def test_sanitizer_strips_script(self) -> None:
        out = sanitize_rich_html(
            '<p>ok</p><script>alert(1)</script><img src="javascript:alert(1)">'
        )
        assert "<script" not in out.lower()
        assert "javascript:" not in out.lower()
        assert "ok" in out
