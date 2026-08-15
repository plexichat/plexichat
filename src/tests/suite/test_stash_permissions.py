"""Tests for the PlexiStash authorization vocabulary helpers.

``permissions.py`` was previously a misleading contract stub; it now exposes the
canonical permission names plus pure validation helpers that mirror the rules
enforced by ``StashManager``. These tests pin the contract so the vocabulary
cannot drift from the enforcement.
"""

import pytest

from src.core.suite.stash.permissions import (
    PERSONAL_DELEGABLE_PERMISSIONS,
    STASH_PERMISSION_NAMES,
    VALID_SCOPES,
    delegable_on_personal,
    is_valid_permission,
    non_delegable_on_personal,
    validate_permissions,
)


class TestPermissionVocabulary:
    def test_all_manager_permissions_are_known(self):
        # The manager enforces exactly these names (STASH_PERMISSIONS).
        for permission in STASH_PERMISSION_NAMES:
            assert is_valid_permission(permission)

    def test_unknown_permission_is_rejected(self):
        assert not is_valid_permission("admin")
        assert not is_valid_permission("")
        assert not is_valid_permission("view")

    def test_valid_scopes(self):
        assert set(VALID_SCOPES) == {"personal", "server"}


class TestValidatePermissions:
    def test_normalizes_and_dedupes(self):
        assert validate_permissions(["download", "view_metadata", "download"]) == (
            "download",
            "view_metadata",
        )

    def test_rejects_unknown(self):
        with pytest.raises(ValueError):
            validate_permissions(["view_metadata", "sudo"])

    def test_accepts_empty(self):
        assert validate_permissions([]) == ()


class TestPersonalDelegation:
    def test_privileged_permissions_are_not_delegable(self):
        # Personal-item owners must not delegate share/delete/manage_versions:
        # doing so would let a grantee delete or reshare the owner's item.
        privileged = {"share", "delete", "manage_versions"}
        assert not (privileged & PERSONAL_DELEGABLE_PERMISSIONS)

    def test_delegable_on_personal_filters_privileged(self):
        result = delegable_on_personal(
            ["view_metadata", "download", "edit", "share", "delete"]
        )
        assert set(result) == {"view_metadata", "download", "edit"}

    def test_non_delegable_on_personal_returns_privileged(self):
        result = non_delegable_on_personal(
            ["view_metadata", "share", "manage_versions"]
        )
        assert set(result) == {"share", "manage_versions"}
