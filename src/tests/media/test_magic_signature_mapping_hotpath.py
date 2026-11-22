"""The magic-signature table must not be rebuilt on every validation call.

``MediaManager._validate_magic_bytes`` runs on every media upload (direct and
chunked). It resolves a content type to its fixed magic-byte signature list.
That list never changes at runtime, so it belongs in a single immutable
module-level mapping. Rebuilding a 19-entry dict (plus a ``min(len(s) ...)``
generator over every signature) per call is pure overhead.

Two independent proofs live here:

* a *per-call operation count*: the function's bytecode must contain no
  mapping-building opcode (``BUILD_MAP``/``BUILD_CONST_KEY_MAP``/``MAP_ADD``).
  Before the fix a 19-key ``BUILD_CONST_KEY_MAP`` is emitted, so this fails.
* a timing ratio: many calls against the precomputed mapping are materially
  faster than the per-call rebuild reference implementation.
"""

import dis
import time

from core.media._validation import _ValidationMixin

_MAP_BUILD_OPS = frozenset({"BUILD_MAP", "BUILD_CONST_KEY_MAP", "MAP_ADD"})


def _count_map_builds(func) -> int:
    return sum(
        1 for instr in dis.get_instructions(func) if instr.opname in _MAP_BUILD_OPS
    )


def _reference_rebuild(file_data: bytes, content_type: str) -> bool:
    """The old per-call behaviour, kept here purely as a timing baseline."""
    magic_signatures = {
        "image/jpeg": [b"\xff\xd8\xff"],
        "image/png": [b"\x89PNG\r\n\x1a\n"],
        "image/gif": [b"GIF87a", b"GIF89a"],
        "image/webp": [b"RIFF"],
        "image/bmp": [b"BM"],
        "image/tiff": [b"II*\x00", b"MM\x00*"],
        "video/mp4": [
            b"\x00\x00\x00\x18ftypmp4",
            b"\x00\x00\x00\x1cftypmp4",
            b"\x00\x00\x00 ftypisom",
            b"ftyp",
        ],
        "video/webm": [b"\x1a\x45\xdf\xa3"],
        "video/quicktime": [b"\x00\x00\x00\x14ftypqt", b"ftypqt"],
        "audio/mpeg": [b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"ID3"],
        "audio/ogg": [b"OggS"],
        "audio/wav": [b"RIFF"],
        "audio/webm": [b"\x1a\x45\xdf\xa3"],
        "application/pdf": [b"%PDF"],
        "application/zip": [b"PK\x03\x04", b"PK\x05\x06"],
        "text/plain": [],
        "text/markdown": [],
        "text/csv": [],
        "application/json": [],
    }
    ct_lower = content_type.lower()
    if ct_lower not in magic_signatures:
        return True
    signatures = magic_signatures[ct_lower]
    if not signatures:
        return True
    shortest_sig_len = min(len(s) for s in signatures)
    if len(file_data) < shortest_sig_len:
        return False
    for sig in signatures:
        if file_data.startswith(sig):
            return True
        if (
            ct_lower in ("video/mp4", "video/quicktime")
            and len(file_data) >= 12
            and b"ftyp" in file_data[:12]
        ):
            return True
    return False


def test_magic_bytes_does_not_rebuild_mapping_per_call():
    """No mapping build in the hot function's bytecode."""
    builds = _count_map_builds(_ValidationMixin._validate_magic_bytes)
    assert builds == 0, (
        "_validate_magic_bytes still rebuilds its signature dict per call "
        f"({builds} map-build bytecode instruction(s)); use a module-level "
        "precomputed immutable mapping"
    )


def test_magic_bytes_behaviour_preserved():
    obj = object.__new__(_ValidationMixin)
    cases = [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png", True),
        (b"not a png", "image/png", False),
        (b"\xff\xd8\xff" + b"\x00" * 64, "image/jpeg", True),
        (b"GIF89a" + b"\x00" * 50, "image/gif", True),
        (b"%PDF-1.7", "application/pdf", True),
        (b"hello world", "text/plain", True),
        (b"anything", "application/x-unknown", True),
        (b"", "image/png", False),
    ]
    for data, content_type, expected in cases:
        assert obj._validate_magic_bytes(data, content_type) is expected


def test_magic_bytes_precomputed_mapping_is_faster():
    """Timing proof: the precomputed mapping beats the per-call rebuild."""
    obj = object.__new__(_ValidationMixin)
    validate = obj._validate_magic_bytes  # bind once for a fair per-call comparison
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ct = "image/png"
    n = 100_000

    # warm both paths
    for _ in range(2_000):
        validate(data, ct)
        _reference_rebuild(data, ct)

    t0 = time.perf_counter()
    for _ in range(n):
        _reference_rebuild(data, ct)
    t_rebuild = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(n):
        validate(data, ct)
    t_precomputed = time.perf_counter() - t0

    print(
        f"\nrebuild={t_rebuild:.4f}s precomputed={t_precomputed:.4f}s "
        f"ratio={t_precomputed / t_rebuild:.3f}"
    )

    # Generous threshold: avoid flakiness while proving a real speedup exists.
    assert t_precomputed < t_rebuild * 0.85, (
        f"expected a material speedup from the precomputed mapping, "
        f"got rebuild={t_rebuild:.4f}s precomputed={t_precomputed:.4f}s"
    )
