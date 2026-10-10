"""Tests for versioned password-pepper secrets stored in a Keyring."""

import pytest

from src.utils.encryption.core.keyring import Keyring


@pytest.mark.unit
@pytest.mark.encryption
class TestKeyringPeppers:
    def test_get_or_create_is_stable(self, tmp_path):
        kr = Keyring(tmp_path / "system_keyring.json")
        first = kr.get_or_create_pepper("v1")
        assert len(first) == 32
        assert kr.get_or_create_pepper("v1") == first

    def test_pepper_survives_reload(self, tmp_path):
        path = tmp_path / "system_keyring.json"
        created = Keyring(path).get_or_create_pepper("v1")
        reloaded = Keyring(path).get_or_create_pepper("v1")
        assert reloaded == created

    def test_ids_are_independent(self, tmp_path):
        kr = Keyring(tmp_path / "system_keyring.json")
        assert kr.get_or_create_pepper("v1") != kr.get_or_create_pepper("v2")
        assert kr.list_peppers() == ["v1", "v2"]

    def test_invalid_id_rejected(self, tmp_path):
        kr = Keyring(tmp_path / "system_keyring.json")
        with pytest.raises(ValueError):
            kr.get_or_create_pepper("no spaces allowed!")
        with pytest.raises(ValueError):
            kr.get_or_create_pepper("")

    def test_peppers_survive_data_key_rotation(self, tmp_path):
        path = tmp_path / "system_keyring.json"
        kr = Keyring(path)
        pepper = kr.get_or_create_pepper("v1")
        kr.rotate()
        assert Keyring(path).get_or_create_pepper("v1") == pepper
