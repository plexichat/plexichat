"""Suite workspace titles, tags and payloads have shared hard bounds."""

import pytest

from src.core.suite.boundaries import (
    MAX_WORKSPACE_DESCRIPTION_LENGTH,
    MAX_WORKSPACE_PAYLOAD_BYTES,
    MAX_WORKSPACE_TAG_COUNT,
    MAX_WORKSPACE_TITLE_LENGTH,
    bound_workspace_payload,
    bound_workspace_tags,
    bound_workspace_text,
)
from src.core.security.hardening import HardeningViolation


class TestWorkspaceText:
    def test_required_and_optional(self):
        assert bound_workspace_text("  Title ", "title", required=True) == "Title"
        assert bound_workspace_text(None, "description") is None

    @pytest.mark.parametrize("bad", ["", "   ", "a" * (MAX_WORKSPACE_TITLE_LENGTH + 1)])
    def test_bad_required_titles(self, bad):
        with pytest.raises(HardeningViolation):
            bound_workspace_text(
                bad, "title", max_length=MAX_WORKSPACE_TITLE_LENGTH, required=True
            )

    def test_control_characters_rejected(self):
        with pytest.raises(HardeningViolation):
            bound_workspace_text("a\nb", "title")

    def test_custom_error_class(self):
        class DomainError(Exception):
            pass

        with pytest.raises(DomainError):
            bound_workspace_text("", "title", required=True, error_cls=DomainError)


class TestWorkspacePayload:
    def test_valid(self):
        assert bound_workspace_payload(b"data") == b"data"

    @pytest.mark.parametrize("bad", [b"", "not-bytes", None])
    def test_invalid(self, bad):
        with pytest.raises(HardeningViolation):
            bound_workspace_payload(bad)

    def test_size_cap(self):
        with pytest.raises(HardeningViolation):
            bound_workspace_payload(b"x" * (MAX_WORKSPACE_PAYLOAD_BYTES + 1))


class TestWorkspaceTags:
    def test_dedupe_and_sort(self):
        assert bound_workspace_tags(["b", "a", "b"]) == ["a", "b"]

    def test_count_cap(self):
        with pytest.raises(HardeningViolation):
            bound_workspace_tags(["x"] * (MAX_WORKSPACE_TAG_COUNT + 1))

    def test_tag_length_cap(self):
        with pytest.raises(HardeningViolation):
            bound_workspace_tags(["x" * 81])

    def test_description_cap(self):
        with pytest.raises(HardeningViolation):
            bound_workspace_text(
                "x" * (MAX_WORKSPACE_DESCRIPTION_LENGTH + 1),
                "description",
                max_length=MAX_WORKSPACE_DESCRIPTION_LENGTH,
            )


class TestSuiteAppsUseSharedBounds:
    def test_workbooks_use_shared_constants(self):
        import src.core.suite.sum.manager_workbooks as wb

        assert wb.MAX_WORKSPACE_TITLE_LENGTH == MAX_WORKSPACE_TITLE_LENGTH
        assert wb.MAX_WORKSPACE_PAYLOAD_BYTES == MAX_WORKSPACE_PAYLOAD_BYTES

    def test_stash_uses_shared_constants(self):
        import src.core.suite.stash.stash_manager_items.creation as creation

        assert creation.MAX_WORKSPACE_TITLE_LENGTH == MAX_WORKSPACE_TITLE_LENGTH
        assert (
            creation.MAX_WORKSPACE_DESCRIPTION_LENGTH
            == MAX_WORKSPACE_DESCRIPTION_LENGTH
        )
