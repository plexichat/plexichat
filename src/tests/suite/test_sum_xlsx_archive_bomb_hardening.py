"""Regression tests for XLSX archive metadata bombs on the import path.

``_reject_zip_bomb`` must refuse an archive from its central directory alone:
a handful of bytes of deflate stream can declare tens of megabytes of
worksheet XML, and an archive with a huge member count burns time and memory
while being parsed. The absolute byte caps alone do not stop either shape.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from src.core.suite.sum._shared import (
    MAX_XLSX_ARCHIVE_ENTRIES,
    MAX_XLSX_MEMBER_BYTES,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    XLSX_RATIO_MIN_OUTPUT_BYTES,
    SumValidationError,
)
from src.core.suite.sum.manager_exchange import SumManagerExchange

_NAMESPACE = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _base_xlsx() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    buf = io.BytesIO()
    openpyxl.Workbook().save(buf)
    return buf.getvalue()


def _xlsx_with_sheet_payload(payload: str, *, stored: bool = False) -> bytes:
    """Return a valid-shaped XLSX whose worksheet member is *payload*."""
    source = zipfile.ZipFile(io.BytesIO(_base_xlsx()))
    target = io.BytesIO()
    method = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(target, "w", method) as archive:
        for item in source.infolist():
            data = (
                payload.encode("utf-8")
                if item.filename == "xl/worksheets/sheet1.xml"
                else source.read(item.filename)
            )
            archive.writestr(item.filename, data)
    source.close()
    return target.getvalue()


def _sheet_with_text(nbytes: int) -> str:
    return (
        '<?xml version="1.0"?><worksheet '
        + _NAMESPACE
        + '><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>'
        + ("x" * nbytes)
        + "</t></is></c></row></sheetData></worksheet>"
    )


def test_ratio_bomb_within_absolute_caps_is_rejected():
    """A tiny compressed member that inflates hugely must fail the ratio check.

    The declared output is deliberately kept *below* both absolute caps, so a
    pass here would prove the old byte-ceiling-only check was insufficient.
    """
    raw = _xlsx_with_sheet_payload(_sheet_with_text(12 * 1024 * 1024))
    infos = zipfile.ZipFile(io.BytesIO(raw)).infolist()
    declared = sum(i.file_size for i in infos)
    assert all(i.file_size <= MAX_XLSX_MEMBER_BYTES for i in infos)
    assert declared <= MAX_XLSX_UNCOMPRESSED_BYTES  # old checks would allow it
    assert declared > XLSX_RATIO_MIN_OUTPUT_BYTES
    assert len(raw) < declared // 256

    with pytest.raises(SumValidationError, match="expansion ratio"):
        SumManagerExchange._reject_zip_bomb(raw)


def test_entry_count_bomb_is_rejected():
    """A member-count bomb is refused even though its total bytes are tiny."""
    source = zipfile.ZipFile(io.BytesIO(_base_xlsx()))
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        for item in source.infolist():
            archive.writestr(item.filename, source.read(item.filename))
        for i in range(MAX_XLSX_ARCHIVE_ENTRIES + 1 - len(source.infolist())):
            archive.writestr(f"junk/f{i}.bin", b"x")
    source.close()
    raw = target.getvalue()
    assert (
        len(zipfile.ZipFile(io.BytesIO(raw)).infolist()) == MAX_XLSX_ARCHIVE_ENTRIES + 1
    )

    with pytest.raises(SumValidationError, match="too many entries"):
        SumManagerExchange._reject_zip_bomb(raw)


def test_ordinary_workbook_still_accepted():
    """A normal XLSX, including a modestly compressible sheet, is not refused."""
    SumManagerExchange._reject_zip_bomb(_base_xlsx())
    # A 1 MiB highly compressible cell is below the ratio floor and stays
    # allowed; the floor prevents false positives on small workbooks.
    small = _xlsx_with_sheet_payload(_sheet_with_text(1024 * 1024))
    SumManagerExchange._reject_zip_bomb(small)
