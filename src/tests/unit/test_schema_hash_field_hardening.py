"""Regression: hash-carrying request fields are bounded and control-char clean.

Two real gaps were closed at the schema boundary:

* ``AttachmentRequest.hash`` had no length ceiling and no character rule even
  though it is persisted verbatim as the attachment ``checksum`` and used in
  hash/dedup lookups (``src/api/routes/messages/messages_crud/send.py``).
* ``HashReportRequest.hash_value`` / ``phash_value`` feed the hash-report store
  and lookup keys (``media.report_hash`` / ``is_hash_blocked``) but accepted
  arbitrary control and bidi/format characters; ``phash_value`` also accepted
  any non-hex string despite being consumed as a hex digest.

These tests pin the new behaviour without weakening any legitimate input: real
hex digests and a blank ``phash_value`` (clear) must all still pass.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.media import HashReportRequest
from src.api.schemas.messages import AttachmentRequest

pytestmark = [pytest.mark.unit, pytest.mark.security]


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
    payload = {
        "hash_value": "a" * 64,
        "reason": "solid report",
    }
    payload.update(overrides)
    return payload


# --- AttachmentRequest.hash -------------------------------------------------


def test_attachment_hash_accepts_a_real_hex_digest():
    model = AttachmentRequest.model_validate(_attachment(hash="a" * 64))
    assert model.hash == "a" * 64


def test_attachment_hash_is_length_bounded():
    with pytest.raises(ValidationError):
        AttachmentRequest.model_validate(_attachment(hash="a" * 129))


def test_attachment_hash_rejects_control_and_bidi_characters():
    for hostile in ("a" * 32 + "\x00", "a" * 32 + "\u202e", "a" * 32 + "\u2066"):
        with pytest.raises(ValidationError):
            AttachmentRequest.model_validate(_attachment(hash=hostile))


def test_attachment_hash_stays_optional():
    assert AttachmentRequest.model_validate(_attachment()).hash is None


# --- HashReportRequest.hash_value / phash_value ----------------------------


def test_hash_report_accepts_a_hex_hash():
    assert HashReportRequest.model_validate(_report()).hash_value == "a" * 64
    assert (
        HashReportRequest.model_validate(_report(hash_value="b" * 32)).hash_value
        == "b" * 32
    )


def test_hash_report_rejects_control_and_bidi_characters_in_hash_value():
    for hostile in ("a" * 16 + "\x00", "a" * 16 + "\x1b", "a" * 16 + "\u202e"):
        with pytest.raises(ValidationError):
            HashReportRequest.model_validate(_report(hash_value=hostile))


def test_hash_report_phash_accepts_hex_and_blank_clear():
    assert (
        HashReportRequest.model_validate(_report(phash_value="b" * 16)).phash_value
        == "b" * 16
    )
    assert HashReportRequest.model_validate(_report(phash_value="")).phash_value == ""


def test_hash_report_phash_rejects_non_hex_control_and_unanchored_values():
    for hostile in (
        "zzzz",  # not hex
        "prefix-" + "a" * 16,  # contains hex but is not anchored hex
        "a" * 16 + "\x00",  # control character
        "a" * 65,  # over max_length=64
    ):
        with pytest.raises(ValidationError):
            HashReportRequest.model_validate(_report(phash_value=hostile))
