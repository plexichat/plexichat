"""The bots license gate must fail closed when licensing cannot be imported."""

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.applications.exceptions import LicenseFeatureError
from src.core.applications.manager.bot_management import BotManagementMixin


class _LicenseProbe(BotManagementMixin):
    """Minimal host for the license helper; it does not touch ``self``."""


def test_check_bot_license_denies_when_licensing_module_is_unavailable():
    with patch.object(
        importlib, "import_module", side_effect=ImportError("licensing unavailable")
    ):
        with pytest.raises(LicenseFeatureError) as excinfo:
            _LicenseProbe()._check_bot_license()
    assert excinfo.value.feature == "bots"


def test_check_bot_license_still_allows_a_licensed_feature():
    licensed = SimpleNamespace(has_feature=lambda feature, default=False: True)
    with patch.object(importlib, "import_module", return_value=licensed):
        assert _LicenseProbe()._check_bot_license() is True


def test_check_bot_license_still_denies_an_unlicensed_feature():
    unlicensed = SimpleNamespace(has_feature=lambda feature, default=False: False)
    with patch.object(importlib, "import_module", return_value=unlicensed):
        with pytest.raises(LicenseFeatureError):
            _LicenseProbe()._check_bot_license()


def test_approve_bot_denies_when_license_check_cannot_be_evaluated():
    class _Manager(BotManagementMixin):
        def __init__(self):
            self._db = MagicMock()
            self._require_server_manage_permission = MagicMock()

    manager = _Manager()
    with patch.object(
        importlib, "import_module", side_effect=ImportError("licensing unavailable")
    ):
        with pytest.raises(LicenseFeatureError) as excinfo:
            manager.approve_bot(100, 200, 300)

    assert excinfo.value.feature == "bots"
    # The gate must stop the approval before any database write; the old
    # fail-open path returned True here and proceeded to insert the bot.
    manager._db.execute.assert_not_called()
