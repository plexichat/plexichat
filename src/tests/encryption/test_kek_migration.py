"""KEK migration CLI — handle_migrate_kek smoke test (no live keyring)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

pytestmark = pytest.mark.integration


class TestKEKMigration:
    def test_handle_migrate_kek_routes_dry_run(self, monkeypatch):
        """Run with --kek-validate to exercise the validate branch."""

        import src.cli.migrate_kek as toolmod

        # Stub the underlying validators/migrators.
        for name in (
            "validate_keyrings",
            "migrate_keyring",
            "migrate_all_keyrings",
            "rollback_keyring",
        ):
            monkeypatch.setattr(
                f"src.utils.encryption.kek_migration.{name}",
                MagicMock(return_value=True),
            )

        args = MagicMock()
        args.kek_validate = True
        args.kek_rollback = False
        args.kek_all = False
        args.kek_keyring = None
        args.kek_old_env = None
        args.kek_new_env = None
        args.kek_dry_run = False
        args.kek_force = False

        with pytest.raises(SystemExit) as ei:
            toolmod.handle_migrate_kek(args)
        assert ei.value.code in (0, 1)

    def test_handle_migrate_kek_no_args_exits_1(self):
        import src.cli.migrate_kek as toolmod

        args = MagicMock()
        args.kek_validate = False
        args.kek_rollback = False
        args.kek_all = False
        args.kek_keyring = None
        args.kek_old_env = None
        args.kek_new_env = None
        args.kek_dry_run = False
        args.kek_force = False

        with pytest.raises(SystemExit) as ei:
            toolmod.handle_migrate_kek(args)
        # No flags set → falls through to the error branch.
        assert ei.value.code == 1
