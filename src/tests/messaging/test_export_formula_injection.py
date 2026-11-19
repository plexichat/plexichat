"""Regression tests: CSV transcript export must never emit formula cells.

Message content is attacker-controlled free text. A downloaded CSV opened in
Excel/Sheets evaluates a cell whose first character is ``=``, ``+``, ``-``,
``@``, tab or CR as a formula (e.g. ``=HYPERLINK(...)`` / ``=cmd|...``).
The transcript CSV export must neutralise every such value before writing it.
"""

import csv
import io

from src.core.messaging.services.export import TranscriptExportService


_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _message(content, **overrides):
    msg = {
        "id": "1000",
        "author_id": "42",
        "content": content,
        "created_at": 1700000000000,
        "edited_at": None,
        "reply_to_id": None,
    }
    msg.update(overrides)
    return msg


def _parse(content):
    return list(csv.reader(io.StringIO(content)))


def test_csv_export_neutralises_formula_leads():
    payloads = [
        '=HYPERLINK("http://evil.example","click")',
        "=1+1",
        "+cmd|'/c calc'!A0",
        "@SUM(1+1)",
        "-1+1",
        "\t=1+1",  # leading tab is stripped by spreadsheets before eval
        "\r=1+1",  # leading CR likewise
        "  =1+1",  # leading spaces before the trigger
        "\t@SUM(1+1)",
    ]
    messages = [_message(p, id=p, author_id=p, reply_to_id=p) for p in payloads]
    csv_text, mime = TranscriptExportService(None, None, None)._generate_csv(
        messages, "42"
    )
    assert mime == "text/csv"

    rows = _parse(csv_text)
    assert rows[0] == [
        "id",
        "author_id",
        "content",
        "created_at",
        "edited_at",
        "reply_to_id",
    ]
    assert len(rows) == len(payloads) + 1

    # Every data cell that originally carried an attacker value must now be a
    # literal string, never a value a spreadsheet would evaluate as a formula.
    for row in rows[1:]:
        for cell in row:
            probe = cell.lstrip(" \t\r\n")
            assert not (probe and probe[0] in _LEADS), row
        assert any(cell.startswith("'") for cell in row), row


def test_csv_export_preserves_benign_values():
    # Numbers and ordinary text keep their meaning; no over-quoting.
    messages = [
        _message("hello world", created_at=1700000000123, edited_at=1700000000999),
        _message("plain @ sign in the middle"),
        _message("-5 degrees is fine here"),
    ]
    csv_text, _ = TranscriptExportService(None, None, None)._generate_csv(
        messages, "42"
    )
    rows = _parse(csv_text)
    assert rows[1][2] == "hello world"
    assert rows[1][3] == "1700000000123"
    assert rows[1][4] == "1700000000999"
    assert rows[2][2] == "plain @ sign in the middle"
    # A leading '-' is always neutralised, even in otherwise-innocent text.
    assert rows[3][2] == "'-5 degrees is fine here"
