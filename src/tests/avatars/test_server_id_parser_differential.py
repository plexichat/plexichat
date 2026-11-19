"""Avatar server-id parsing must use the shared ASCII snowflake gate.

``parse_server_id`` used to call bare ``int(server_id)``. Python's ``int``
accepts Unicode decimal homoglyphs (``"１２"`` -> 12), PEP 515 separators
(``"1_2"`` -> 12), a leading sign/zero, ``0`` and arbitrarily large integers.
That normalized id then reached server-icon storage paths and permission
checks as a value different from the one the caller wrote, and disagreed with
every other route boundary, which parses ids with ``parse_id``. It now uses
that canonical gate and rejects non-canonical ids with ``AvatarValidationError``.
"""

import pytest

from src.api.routes.avatars.errors import AvatarValidationError
from src.api.routes.avatars.validation import parse_server_id
from src.core.security.hardening import SNOWFLAKE_MAX

# All accepted by int() (or raising a bare ValueError) but rejected by the
# bounded ASCII-only gate.
_BAD_IDS = [
    "0",  # zero is not a valid snowflake
    "-1",  # negative
    str(SNOWFLAKE_MAX + 1),  # just past the signed 64-bit ceiling
    str(2**80),  # far past it (must not 500 on conversion)
    "9" * 5000,  # oversized token must fail validation, not int()'s digit cap
    "1_2",  # PEP 515 underscore -> int 12
    "٤٢",  # Arabic-Indic digits -> int 42
    "１２",  # fullwidth digits -> int 12
    "1.5",  # fraction int() would reject with a bare ValueError
    "0x10",  # non-decimal prefix int() rejects with a bare ValueError
    "../7",  # path traversal marker
    "1/2",  # separator
]


@pytest.mark.parametrize("bad_id", _BAD_IDS)
def test_parse_server_id_rejects_noncanonical_id(bad_id):
    with pytest.raises(AvatarValidationError):
        parse_server_id(bad_id)


@pytest.mark.parametrize("raw", ["1", "7", "42", str(SNOWFLAKE_MAX), "  123  ", "+9"])
def test_canonical_server_ids_parse_unchanged(raw):
    assert parse_server_id(raw) == int(raw.strip())
