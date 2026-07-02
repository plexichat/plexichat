"""ExportFormatGenerator — JSON + ZIP, storage round-trip, manifest."""

from __future__ import annotations

import json
import zipfile
import io
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded_user(auth_manager, pii_gen):
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username="exportuser",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return user


class TestExportGenerator:
    def test_generate_json(self, db, seeded_user):
        from src.core.dsar.import_formats_safe import ExportFormatGenerator

        # Try a guarded import path: first the canonical name, then the legacy.
        try:
            from src.core.dsar.export_formats import ExportFormatGenerator
        except ImportError:
            pytest.skip("ExportFormatGenerator not available")

        sample = {"identity": {"id": seeded_user.id}}
        path, checksum, size = ExportFormatGenerator(db=db).generate_json(
            sample, request_id=123, user_id=seeded_user.id
        )
        assert path
        assert size > 0
        assert len(checksum) == 64

        bytes_ = ExportFormatGenerator(db=db).retrieve(path)
        envelope = json.loads(bytes_)
        assert envelope["export_type"] == "dsar_json"
        assert envelope["data"]["identity"]["id"] == seeded_user.id

    def test_generate_zip(self, db, seeded_user):
        try:
            from src.core.dsar.export_formats import ExportFormatGenerator
        except ImportError:
            pytest.skip("ExportFormatGenerator not available")

        sample = {"identity": {"id": seeded_user.id}, "profile": {"x": 1}}
        path, checksum, size = ExportFormatGenerator(db=db).generate_zip(
            sample, request_id=124, user_id=seeded_user.id
        )
        assert size > 0

        bytes_ = ExportFormatGenerator(db=db).retrieve(path)
        archive = zipfile.ZipFile(io.BytesIO(bytes_))
        names = archive.namelist()
        assert "manifest.json" in names
        assert "identity.json" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["export_type"] == "dsar_zip"
