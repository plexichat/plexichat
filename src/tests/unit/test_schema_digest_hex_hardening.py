"""Regression: digest-carrying request fields must be anchored hex tokens.

A content hash is a machine value, not free text. These fields are persisted
verbatim (attachment ``checksum``, hash-report keys, blocklist keys, stash
version checksums) and reused as lookup keys in dedup/hash queries or rendered
in admin review views. Bounding their length and stripping control characters
is not enough: a value such as ``"xx<img src=x onerror=...>"`` is control-char
clean and fits in 16-128 characters, yet it is not a digest at all.

This pins the boundary rule for the fields the hash hardening sweep left with
only a length/control check. Real digests (SHA-256, perceptual hashes of 16/32
and 64 hex chars) and the optional/clear forms must still pass unchanged.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.admin.hash_reports import ManualBlockHashRequest
from src.api.schemas.common import require_hex_digest
from src.api.schemas.media import HashReportRequest
from src.api.schemas.messages import AttachmentRequest
from src.api.schemas.stash import StashVersionCreateRequest

pytestmark = [pytest.mark.unit, pytest.mark.security]


# Values that are *not* digests but used to sail through a length/control check.
HOSTILE = (
    "<img src=x onerror=alert(1)>",  # markup, no control chars, within bounds
    "zzzz" + "a" * 28,  # non-hex characters
    "prefix-" + "a" * 32,  # contains hex but is not anchored
    "a" * 32 + " ",  # trailing whitespace
    "a" * 32 + "\x00",  # C0 control
    "a" * 32 + "\u202e",  # bidi override
    "a" * 32 + "\u2066",  # bidi isolate
)


def _attachment(**overrides):
    payload = {
        "filename": "photo.png",
        "content_type": "image/png",
        "size": 1,
        "url": "https://example.test/photo.png",
    }
    payload.update(overrides)
    return payload


def _report(**overrides):
    payload = {"hash_value": "a" * 64, "reason": "solid report"}
    payload.update(overrides)
    return payload


# --- shared helper ---------------------------------------------------------


def test_hex_digest_helper_accepts_real_digests_and_clear_values():
    assert require_hex_digest(None, "hash") is None
    assert require_hex_digest("", "hash") == ""
    assert require_hex_digest("a" * 64, "hash") == "a" * 64
    assert require_hex_digest("A" * 16, "hash") == "A" * 16
    assert require_hex_digest("0123456789abcdef" * 2, "hash") == "0123456789abcdef" * 2


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hex_digest_helper_rejects_non_digest_values(hostile):
    with pytest.raises(ValueError):
        require_hex_digest(hostile, "hash")


# --- HashReportRequest.hash_value ------------------------------------------


def test_hash_report_accepts_sha256_and_perceptual_hex():
    assert HashReportRequest.model_validate(_report()).hash_value == "a" * 64
    for length in (16, 32, 64, 128):
        value = "b" * length
        assert (
            HashReportRequest.model_validate(_report(hash_value=value)).hash_value
            == value
        )


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hash_report_rejects_non_hex_hash_value(hostile):
    with pytest.raises(ValidationError):
        HashReportRequest.model_validate(_report(hash_value=hostile))


# --- AttachmentRequest.hash ------------------------------------------------


def test_attachment_hash_accepts_a_real_hex_digest():
    assert AttachmentRequest.model_validate(_attachment(hash="a" * 64)).hash == "a" * 64
    assert AttachmentRequest.model_validate(_attachment()).hash is None


@pytest.mark.parametrize("hostile", HOSTILE)
def test_attachment_hash_rejects_non_hex_values(hostile):
    with pytest.raises(ValidationError):
        AttachmentRequest.model_validate(_attachment(hash=hostile))


# --- ManualBlockHashRequest.hash_value -------------------------------------


def test_manual_block_hash_accepts_the_supported_digest_lengths():
    for length in (16, 32, 64):
        value = "c" * length
        model = ManualBlockHashRequest.model_validate(
            {"hash_value": value, "reason": "manual block"}
        )
        assert model.hash_value == value


@pytest.mark.parametrize("hostile", HOSTILE)
def test_manual_block_hash_rejects_non_hex_values(hostile):
    with pytest.raises(ValidationError):
        ManualBlockHashRequest.model_validate(
            {"hash_value": hostile, "reason": "manual block"}
        )


# --- StashVersionCreateRequest.checksum ------------------------------------


def test_stash_version_checksum_accepts_hex_and_clear():
    assert StashVersionCreateRequest().checksum is None
    assert StashVersionCreateRequest(checksum="d" * 64).checksum == "d" * 64


@pytest.mark.parametrize("hostile", HOSTILE)
def test_stash_version_checksum_rejects_non_hex_values(hostile):
    with pytest.raises(ValidationError):
        StashVersionCreateRequest(checksum=hostile)
