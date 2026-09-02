"""Bootstrap admin-credential handling must never persist plaintext by default.

Regression coverage for the findings.md H5 (7.2): the generated first-boot
admin password used to be written to ~/.plexichat/admin_credentials.txt on
every first boot. The password now goes to the operator console exactly once;
a disk file exists only behind the explicit PLEXICHAT_SAVE_ADMIN_CREDENTIALS=1
opt-in.
"""

import builtins
from unittest.mock import MagicMock, patch


class TestBootstrapCredentialHandling:
    """_save_admin_credentials is console-once, disk opt-in."""

    def test_default_does_not_create_credential_file(self, tmp_path, monkeypatch):
        import src.core.admin.auth.helpers as helpers

        monkeypatch.delenv("PLEXICHAT_SAVE_ADMIN_CREDENTIALS", raising=False)
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        printed = []
        fake_print = print

        def capture_print(*args, **kwargs):
            printed.append(" ".join(str(a) for a in args))
            fake_print(*args, **kwargs)

        with patch.object(builtins, "print", capture_print):
            helpers._save_admin_credentials("super-secret-password")

        assert not (tmp_path / ".plexichat" / "admin_credentials.txt").exists()
        assert any("super-secret-password" in p for p in printed)

    def test_opt_in_writes_mode_0600_file(self, tmp_path, monkeypatch):
        import os

        import src.core.admin.auth.helpers as helpers

        monkeypatch.setenv("PLEXICHAT_SAVE_ADMIN_CREDENTIALS", "1")
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        helpers._save_admin_credentials("super-secret-password")

        creds = tmp_path / ".plexichat" / "admin_credentials.txt"
        assert creds.exists()
        assert (creds.stat().st_mode & 0o777) == 0o600
        assert "super-secret-password" in creds.read_text()

    def test_opt_in_failure_is_fatal_not_swallowed(self, tmp_path, monkeypatch):
        """A failed secure write must raise, never silently drop the password."""
        import src.core.admin.auth.helpers as helpers

        monkeypatch.setenv("PLEXICHAT_SAVE_ADMIN_CREDENTIALS", "1")
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        with patch.object(
            helpers.tempfile, "mkstemp", side_effect=OSError("disk full")
        ):
            try:
                helpers._save_admin_credentials("pw")
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError on failed secure write")

    def test_password_change_rotates_no_credential_file_writes(
        self, tmp_path, monkeypatch
    ):
        """Only the bootstrap path may touch the (opt-in) file; rotation never does."""
        import src.core.admin.auth.helpers as helpers

        monkeypatch.delenv("PLEXICHAT_SAVE_ADMIN_CREDENTIALS", raising=False)
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        rotation_helpers = MagicMock()
        helpers._sync_password_to_auth_users(rotation_helpers, "admin", "h")
        assert not (tmp_path / ".plexichat").exists()
