"""Bootstrap admin-credential handling persists plaintext by default.

Regression coverage for the findings.md H5 (7.2): the generated first-boot
admin password is written to ~/.plexichat/admin_credentials.txt (mode 0600)
on every first boot unless the operator explicitly opts out with
PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS=1. The password always goes to the
operator console exactly once.
"""

import builtins
from unittest.mock import MagicMock, patch


class TestBootstrapCredentialHandling:
    """_save_admin_credentials is console-once, disk opt-out."""

    def test_default_creates_credential_file(self, tmp_path, monkeypatch):
        import src.core.admin.auth.helpers as helpers

        monkeypatch.delenv("PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS", raising=False)
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        printed = []
        fake_print = print

        def capture_print(*args, **kwargs):
            printed.append(" ".join(str(a) for a in args))
            fake_print(*args, **kwargs)

        with patch.object(builtins, "print", capture_print):
            helpers._save_admin_credentials("super-secret-password")

        assert (tmp_path / ".plexichat" / "admin_credentials.txt").exists()
        assert any("super-secret-password" in p for p in printed)

    def test_opt_out_prevents_credential_file(self, tmp_path, monkeypatch):
        import src.core.admin.auth.helpers as helpers

        monkeypatch.setenv("PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS", "1")
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        printed = []
        fake_print = print

        def capture_print(*args, **kwargs):
            printed.append(" ".join(str(a) for a in args))
            fake_print(*args, **kwargs)

        with patch.object(builtins, "print", capture_print):
            helpers._save_admin_credentials("super-secret-password")

        assert not (tmp_path / ".plexichat" / "admin_credentials.txt").exists()
        printed_text = "\n".join(printed)
        assert "super-secret-password" in printed_text

    def test_opt_out_failure_is_still_fatal_when_enabled(self, tmp_path, monkeypatch):
        """When not opted out, a failed secure write must raise, never silently drop."""
        import src.core.admin.auth.helpers as helpers

        monkeypatch.delenv("PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS", raising=False)
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
        """Only the bootstrap path may touch the credential file; rotation never does."""
        import src.core.admin.auth.helpers as helpers

        monkeypatch.delenv("PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS", raising=False)
        monkeypatch.setattr(helpers.Path, "home", staticmethod(lambda: tmp_path))

        rotation_helpers = MagicMock()
        helpers._sync_password_to_auth_users(rotation_helpers, "admin", "h")
        assert not (tmp_path / ".plexichat").exists()
