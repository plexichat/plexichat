"""Dense column segment storage for PlexiSum.

Segments pack column cells into compressed blobs stored in
``sum_column_segments``. The segment size is fixed at 1000 rows so that
viewport reads can decode only the relevant range instead of the full column.
"""

import json
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

SEGMENT_SIZE = 1000


class SegmentEncoder:
    """Pack / unpack column cells into compressed segments."""

    # ── encoding strategies ──────────────────────────────────────────────

    @staticmethod
    def _encode_plain(cells: List[Dict[str, Any]]) -> bytes:
        return json.dumps(cells, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _decode_plain(data: bytes) -> List[Dict[str, Any]]:
        return json.loads(data)

    @staticmethod
    def _encode_runlength(cells: List[Dict[str, Any]]) -> bytes:
        """Encode consecutive equal-value cells as [{value, count}] runs."""
        runs: List[Dict[str, Any]] = []
        rows: List[int] = []
        for cell in cells:
            key = (
                cell.get("value_type"),
                cell.get("value_json"),
                cell.get("formula"),
                cell.get("style_json"),
            )
            rows.append(int(cell.get("row_index", cell.get("row", 0))))
            if runs and runs[-1]["key"] == key:
                runs[-1]["count"] += 1
            else:
                runs.append({"key": key, "count": 1, "cell": cell})
        # If run-length is not shorter than plain, fall back
        encoded_runs = [{"value": run["cell"], "count": run["count"]} for run in runs]
        rl_bytes = json.dumps(
            {"runs": encoded_runs, "rows": rows}, separators=(",", ":")
        ).encode("utf-8")
        plain_bytes = SegmentEncoder._encode_plain(cells)
        if len(rl_bytes) >= len(plain_bytes):
            return b"\x00" + plain_bytes
        return b"\x01" + rl_bytes

    @staticmethod
    def _decode_runlength(data: bytes) -> List[Dict[str, Any]]:
        if data[0:1] == b"\x00":
            return SegmentEncoder._decode_plain(data[1:])
        info = json.loads(data[1:])
        # New format: {"runs": [...], "rows": [...]}; legacy blobs were a bare
        # list of runs and had no row indices (lossy — treat as sequential).
        if isinstance(info, dict):
            runs = info.get("runs", [])
            rows = info.get("rows")
        else:
            runs = info
            rows = None
        cells: List[Dict[str, Any]] = []
        position = 0
        for run in runs:
            cell = run["value"]
            for _ in range(run["count"]):
                out = dict(cell)
                if rows is not None:
                    out["row_index"] = int(rows[position])
                cells.append(out)
                position += 1
        return cells

    @staticmethod
    def _encode_delta(cells: List[Dict[str, Any]]) -> bytes:
        """Encode sequential number cells as [base, ...deltas]."""
        numbers = []
        rows: List[int] = []
        for cell in cells:
            if (
                cell.get("value_type") != "number"
                or cell.get("style_json") is not None
                or cell.get("formula") is not None
            ):
                return b"\x00" + SegmentEncoder._encode_plain(cells)
            numbers.append(float(cell.get("value_json", 0)))
            rows.append(int(cell.get("row_index", cell.get("row", 0))))
        if not numbers:
            return b"\x00" + SegmentEncoder._encode_plain(cells)
        deltas = [numbers[0]]
        for i in range(1, len(numbers)):
            deltas.append(numbers[i] - numbers[i - 1])
        result = json.dumps(
            {
                "base": numbers[0],
                "deltas": deltas,
                "count": len(numbers),
                "rows": rows,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        plain_bytes = SegmentEncoder._encode_plain(cells)
        if len(result) >= len(plain_bytes):
            return b"\x00" + plain_bytes
        return b"\x02" + result

    @staticmethod
    def _decode_delta(
        data: bytes, row_start: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if data[0:1] == b"\x00":
            return SegmentEncoder._decode_plain(data[1:])
        info = json.loads(data[1:])
        base = info["base"]
        deltas = info["deltas"]
        rows = info.get("rows")
        numbers = [base]
        for d in deltas[1:]:
            numbers.append(numbers[-1] + d)
        cells = []
        for i, num in enumerate(numbers):
            if rows is not None:
                row_index = int(rows[i])
            elif row_start is not None:
                row_index = row_start + i
            else:
                row_index = i
            cells.append(
                {
                    "row_index": row_index,
                    "value_type": "number",
                    "value_json": json.dumps(num),
                    "formula": None,
                    "style_json": None,
                }
            )
        return cells

    @staticmethod
    def _encode_dictionary(cells: List[Dict[str, Any]]) -> bytes:
        """Encode low-cardinality text cells by storing unique values once."""
        values = []
        rows = []
        for cell in cells:
            values.append(
                (
                    cell.get("value_type"),
                    cell.get("value_json"),
                    cell.get("formula"),
                    cell.get("style_json"),
                )
            )
            rows.append(int(cell.get("row_index", cell.get("row", 0))))
        unique = list(dict.fromkeys(values))
        if len(unique) >= len(values) * 0.7:
            return b"\x00" + SegmentEncoder._encode_plain(cells)
        lookup = {v: i for i, v in enumerate(unique)}
        indices = [lookup[v] for v in values]
        result = json.dumps(
            {
                "unique": [
                    {
                        "value_type": u[0],
                        "value_json": u[1],
                        "formula": u[2],
                        "style_json": u[3],
                    }
                    for u in unique
                ],
                "indices": indices,
                "rows": rows,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        plain_bytes = SegmentEncoder._encode_plain(cells)
        if len(result) >= len(plain_bytes):
            return b"\x00" + plain_bytes
        return b"\x03" + result

    @staticmethod
    def _decode_dictionary(data: bytes) -> List[Dict[str, Any]]:
        if data[0:1] == b"\x00":
            return SegmentEncoder._decode_plain(data[1:])
        info = json.loads(data[1:])
        unique = info["unique"]
        indices = info["indices"]
        rows = info.get("rows")
        cells = []
        for position, idx in enumerate(indices):
            entry = unique[idx]
            cells.append(
                {
                    "row_index": int(rows[position]) if rows is not None else position,
                    "value_type": entry["value_type"],
                    "value_json": entry["value_json"],
                    "formula": entry["formula"],
                    "style_json": entry.get("style_json"),
                }
            )
        return cells

    @staticmethod
    def _detect_encoding(cells: List[Dict[str, Any]]) -> str:
        """Auto-detect the best encoding for a batch of cells."""
        if not cells:
            return "plain"
        # Check if all number cells -> delta
        all_numbers = all(c.get("value_type") == "number" for c in cells)
        if all_numbers and len(cells) >= 10:
            return "delta"
        # Check run-length potential
        values = [
            (
                c.get("value_type"),
                c.get("value_json"),
                c.get("formula"),
                c.get("style_json"),
            )
            for c in cells
        ]
        runs = 1
        for i in range(1, len(values)):
            if values[i] != values[i - 1]:
                runs += 1
        if runs < len(cells) * 0.3 and len(cells) >= 10:
            return "runlength"
        # Check dictionary potential
        unique_count = len(set(values))
        if unique_count < len(cells) * 0.3 and unique_count < 100 and len(cells) >= 10:
            return "dictionary"
        return "plain"

    # ── public API ───────────────────────────────────────────────────────

    @staticmethod
    def encode_segment(
        cells: List[Dict[str, Any]],
        encoding: Optional[str] = None,
    ) -> bytes:
        """Encode a batch of cells into a compressed segment.

        Returns JSON bytes: ``{encoding, cells, row_start, row_end, statistics}``.
        """
        if not cells:
            segment = {
                "encoding": "plain",
                "cells": [],
                "row_start": 0,
                "row_end": 0,
                "statistics": {"count": 0},
            }
            return json.dumps(segment, separators=(",", ":")).encode("utf-8")

        row_start = min(int(c.get("row_index", c.get("row", 0))) for c in cells)
        row_end = max(int(c.get("row_index", c.get("row", 0))) for c in cells) + 1

        if encoding is None:
            encoding = SegmentEncoder._detect_encoding(cells)

        encoder_map = {
            "plain": SegmentEncoder._encode_plain,
            "runlength": SegmentEncoder._encode_runlength,
            "delta": SegmentEncoder._encode_delta,
            "dictionary": SegmentEncoder._encode_dictionary,
        }
        encoder = encoder_map.get(encoding, SegmentEncoder._encode_plain)
        compressed = encoder(cells)

        # Build statistics
        value_types = Counter()
        for c in cells:
            vt = c.get("value_type", "blank")
            value_types[vt] += 1

        segment = {
            "encoding": encoding,
            "cells": compressed,
            "row_start": row_start,
            "row_end": row_end,
            "statistics": {
                "count": len(cells),
                "value_types": dict(value_types),
                "compressed_size": len(compressed),
            },
        }
        return json.dumps(
            {
                "encoding": encoding,
                "cells": compressed.decode("latin-1")
                if isinstance(compressed, bytes)
                else compressed,
                "row_start": row_start,
                "row_end": row_end,
                "statistics": segment["statistics"],
            },
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def decode_segment(data: bytes) -> Dict[str, Any]:
        """Decode a segment back to cells."""
        outer = json.loads(data)
        encoding = outer.get("encoding", "plain")
        raw_cells = outer.get("cells", "")
        row_start = outer.get("row_start", 0)

        if isinstance(raw_cells, str):
            cell_bytes = raw_cells.encode("latin-1")
        elif isinstance(raw_cells, (list, tuple)):
            # Empty segments encode ``cells`` as an empty JSON list rather than
            # a latin-1 string; short-circuit instead of decoding an empty blob.
            return {
                "encoding": encoding,
                "cells": list(raw_cells),
                "row_start": row_start,
                "row_end": outer.get("row_end", 0),
                "statistics": outer.get("statistics", {}),
            }
        else:
            cell_bytes = raw_cells

        decoder_map = {
            "plain": SegmentEncoder._decode_plain,
            "runlength": SegmentEncoder._decode_runlength,
            "delta": lambda d: SegmentEncoder._decode_delta(d, row_start),
            "dictionary": SegmentEncoder._decode_dictionary,
        }
        decoder = decoder_map.get(encoding, SegmentEncoder._decode_plain)
        cells = decoder(cell_bytes)

        return {
            "encoding": encoding,
            "cells": cells,
            "row_start": row_start,
            "row_end": outer.get("row_end", 0),
            "statistics": outer.get("statistics", {}),
        }

    @staticmethod
    def pack_column(cells: List[Dict[str, Any]]) -> List[Tuple[str, bytes]]:
        """Pack a full column into segments.

        Returns list of ``(segment_id, encoded_bytes)``.
        """
        if not cells:
            return []

        # Sort cells by row index
        sorted_cells = sorted(
            cells, key=lambda c: int(c.get("row_index", c.get("row", 0)))
        )
        segments: List[Tuple[str, bytes]] = []

        for chunk_start in range(0, len(sorted_cells), SEGMENT_SIZE):
            chunk = sorted_cells[chunk_start : chunk_start + SEGMENT_SIZE]
            row_start = int(chunk[0].get("row_index", chunk[0].get("row", 0)))
            row_end = int(chunk[-1].get("row_index", chunk[-1].get("row", 0))) + 1
            segment_id = f"seg-{row_start}-{row_end}"
            encoded = SegmentEncoder.encode_segment(chunk)
            segments.append((segment_id, encoded))

        return segments

    @staticmethod
    def unpack_column(segments: List[bytes]) -> Dict[int, Dict[str, Any]]:
        """Unpack segments into ``{row_index: cell_dict}`` map."""
        result: Dict[int, Dict[str, Any]] = {}
        for segment_data in segments:
            decoded = SegmentEncoder.decode_segment(segment_data)
            for cell in decoded["cells"]:
                row_index = int(cell.get("row_index", cell.get("row", 0)))
                result[row_index] = cell
        return result
