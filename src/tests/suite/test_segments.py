"""Round-trip tests for PlexiSum column-segment storage.

These exercise the ``SegmentEncoder`` in isolation (no database fixture), with
particular attention to the failure modes the encoder historically had:

* run-length/dictionary encoders keying runs on value only, dropping
  ``style_json`` and ``formula``;
* the delta encoder collapsing sparse numeric rows onto contiguous positions;
* the dictionary decoder emitting sequential ``row_index`` values instead of
  the true sparse rows;
* the delta decoder writing ``value_json`` as a raw float instead of the
  pre-encoded JSON string every other encoder produces.
"""

import json

from src.core.suite.sum.segments import SegmentEncoder, SEGMENT_SIZE


def _cell(row, value_type="number", value=None, formula=None, style=None):
    """Build a segment-ready cell dict with the canonical value_json encoding."""
    cell = {
        "row_index": row,
        "value_type": value_type,
        "value_json": json.dumps(value) if value is not None else None,
        "formula": formula,
        "style_json": json.dumps(style) if style is not None else None,
    }
    return cell


def _roundtrip(cells, encoding=None):
    encoded = SegmentEncoder.encode_segment(cells, encoding=encoding)
    decoded = SegmentEncoder.decode_segment(encoded)
    return decoded


def _norm(cells):
    """Canonicalize decoded cells for order-insensitive comparison."""
    return sorted(
        (
            int(c["row_index"]),
            c.get("value_type"),
            c.get("value_json"),
            c.get("formula"),
            c.get("style_json"),
        )
        for c in cells
    )


def test_plain_roundtrip():
    cells = [
        _cell(0, "number", 1),
        _cell(1, "text", "hello"),
        _cell(2, "boolean", True),
    ]
    decoded = _roundtrip(cells, encoding="plain")
    assert _norm(decoded["cells"]) == _norm(cells)


def test_delta_roundtrip_dense_numbers():
    cells = [_cell(i, "number", float(i * 10)) for i in range(20)]
    encoded = SegmentEncoder.encode_segment(cells, encoding="delta")
    decoded = SegmentEncoder.decode_segment(encoded)
    assert decoded["encoding"] == "delta"
    # value_json must be a JSON string, not a raw float.
    assert all(
        c["value_json"] == json.dumps(float(c["row_index"]) * 10)
        for c in decoded["cells"]
    )
    assert _norm(decoded["cells"]) == _norm(cells)


def test_delta_preserves_sparse_row_indices():
    cells = [_cell(i * 7, "number", float(i)) for i in range(12)]
    encoded = SegmentEncoder.encode_segment(cells, encoding="delta")
    decoded = SegmentEncoder.decode_segment(encoded)
    rows = sorted(int(c["row_index"]) for c in decoded["cells"])
    assert rows == sorted(int(c["row_index"]) for c in cells)


def test_runlength_preserves_style_and_formula():
    # Same value repeated, but with a distinct style/formula on later cells:
    # the encoder must NOT merge differently-keyed cells into one run.
    cells = [
        _cell(0, "number", 5),
        _cell(1, "number", 5, style={"bold": True}),
        _cell(2, "number", 5),
        _cell(3, "formula", None, formula="=1+1"),
    ]
    decoded = _roundtrip(cells, encoding="runlength")
    assert _norm(decoded["cells"]) == _norm(cells)


def test_dictionary_preserves_style_and_sparse_rows():
    cells = []
    rows = [2, 11, 30, 41]
    for pos, row in enumerate(rows):
        style = {"color": "red"} if pos % 2 else None
        cells.append(_cell(row, "text", f"value-{pos % 2}", style=style))
    decoded = _roundtrip(cells, encoding="dictionary")
    assert decoded["encoding"] == "dictionary"
    assert _norm(decoded["cells"]) == _norm(cells)
    rows_out = sorted(int(c["row_index"]) for c in decoded["cells"])
    assert rows_out == sorted(rows)


def test_auto_detection_selects_expected_encoding():
    numbers = [_cell(i, "number", float(i)) for i in range(50)]
    assert SegmentEncoder._detect_encoding(numbers) == "delta"

    repeats = [_cell(i, "text", "same") for i in range(50)]
    assert SegmentEncoder._detect_encoding(repeats) == "runlength"

    low_card = [_cell(i, "text", f"v{i % 3}") for i in range(50)]
    assert SegmentEncoder._detect_encoding(low_card) == "dictionary"


def test_auto_detected_roundtrips_are_lossless():
    cases = [
        [_cell(i, "number", float(i * i)) for i in range(60)],
        [_cell(i, "text", "repeated") for i in range(60)],
        [_cell(i, "text", f"kind-{i % 5}") for i in range(60)],
    ]
    for cells in cases:
        decoded = _roundtrip(cells)
        assert _norm(decoded["cells"]) == _norm(cells)


def test_pack_unpack_column_roundtrip_large():
    cells = []
    for i in range(SEGMENT_SIZE * 2 + 17):
        if i % 3 == 0:
            cells.append(_cell(i, "number", float(i)))
        elif i % 3 == 1:
            cells.append(_cell(i, "text", f"t{i}"))
        else:
            cells.append(_cell(i, "boolean", bool(i % 2)))
    segments = SegmentEncoder.pack_column(cells)
    assert len(segments) == 3
    unpacked = SegmentEncoder.unpack_column([data for _, data in segments])
    assert sorted(unpacked.keys()) == [int(c["row_index"]) for c in cells]
    for cell in cells:
        row = int(cell["row_index"])
        got = unpacked[row]
        assert got["value_type"] == cell["value_type"]
        assert got["value_json"] == cell["value_json"]
        assert got["formula"] == cell["formula"]
        assert got["style_json"] == cell["style_json"]


def test_empty_segment_roundtrip():
    encoded = SegmentEncoder.encode_segment([])
    decoded = SegmentEncoder.decode_segment(encoded)
    assert decoded["cells"] == []
    assert SegmentEncoder.pack_column([]) == []
