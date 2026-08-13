"""
Regression tests for media audit fixes.

Covers:
- MD-8: the URL signer must never fall back to a publicly-known default key;
  an unset / insecure ``signing_key`` is replaced with an ephemeral random key.
- MD-9: ``_read_stream_to_temp`` must delete its temp file if the copy fails.
"""

import os
import tempfile

import pytest


class TestSigningKey:
    def test_insecure_default_signing_key_is_replaced(self, media_module):
        manager = media_module._manager
        manager._config["signing_key"] = "change-this-secret-key"

        signer = manager._init_url_signer()

        # The hardcoded default must no longer be used.
        assert manager._config["signing_key"] != "change-this-secret-key"
        # And the generated signer must actually sign + verify.
        signed = signer.sign_url("/api/v1/media/attachments/x.png", 1)
        assert signer.verify_url(signed.url)[0] is True

    def test_missing_signing_key_generates_ephemeral_key(self, media_module):
        manager = media_module._manager
        manager._config.pop("signing_key", None)

        signer = manager._init_url_signer()

        assert manager._config.get("signing_key")
        signed = signer.sign_url("/api/v1/media/attachments/y.png", 2)
        assert signer.verify_url(signed.url)[0] is True


class TestUploadTempFileCleanup:
    def test_read_stream_to_temp_cleans_up_on_error(self, media_module, monkeypatch):
        recorded = {}
        original = tempfile.NamedTemporaryFile

        def tracking_named_temp_file(*args, **kwargs):
            f = original(*args, **kwargs)
            recorded["name"] = f.name
            return f

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_named_temp_file)

        class BadStream:
            def read(self, n=-1):
                raise IOError("simulated read failure")

        manager = media_module._manager
        with pytest.raises(Exception):
            manager._read_stream_to_temp(BadStream())

        # A temp file was created but must have been removed on the error path.
        assert recorded, "expected a temp file to have been created"
        assert not os.path.exists(recorded["name"])
