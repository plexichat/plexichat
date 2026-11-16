"""Formula parsing and workbook imports are size- and depth-bounded."""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.suite.boundaries import (
    MAX_CSV_IMPORT_BYTES,
    MAX_FORMULA_LENGTH,
    MAX_FORMULA_NESTING_DEPTH,
    MAX_XLSX_IMPORT_BYTES,
    bound_formula_text,
    bound_import_bytes,
    bound_import_text,
)
from src.core.suite.sum._formula_shared import parse_formula


class TestFormulaBounds:
    def test_normal_formula(self):
        assert bound_formula_text("=SUM(A1:A3)") == "=SUM(A1:A3)"

    def test_length_cap(self):
        with pytest.raises(HardeningViolation):
            bound_formula_text("=" + "1+" * (MAX_FORMULA_LENGTH + 1))

    def test_nesting_cap(self):
        with pytest.raises(HardeningViolation):
            bound_formula_text("(" * (MAX_FORMULA_NESTING_DEPTH + 1))

    @pytest.mark.parametrize("bad", [None, 123, "=SUM(A1)\n"])
    def test_bad_formula(self, bad):
        with pytest.raises(HardeningViolation):
            bound_formula_text(bad)

    def test_parser_rejects_deep_formula(self):
        with pytest.raises(HardeningViolation):
            parse_formula(
                "(" * (MAX_FORMULA_NESTING_DEPTH + 1)
                + "1"
                + ")" * (MAX_FORMULA_NESTING_DEPTH + 1)
            )


class TestImportBounds:
    def test_text_import(self):
        assert bound_import_text("a,b", max_bytes=MAX_CSV_IMPORT_BYTES) == "a,b"
        with pytest.raises(HardeningViolation):
            bound_import_text(
                "x" * (MAX_CSV_IMPORT_BYTES + 1), max_bytes=MAX_CSV_IMPORT_BYTES
            )

    def test_byte_import(self):
        assert bound_import_bytes(b"x", max_bytes=10) == b"x"
        with pytest.raises(HardeningViolation):
            bound_import_bytes(b"x" * 11, max_bytes=10)
        with pytest.raises(HardeningViolation):
            bound_import_bytes("x", max_bytes=MAX_XLSX_IMPORT_BYTES)


class TestImportPathsUseBounds:
    def test_modules_reference_shared_constants(self):
        import src.core.suite.sum.manager_data as data
        import src.core.suite.sum.manager_exchange as exchange

        assert data.MAX_CSV_IMPORT_BYTES == MAX_CSV_IMPORT_BYTES
        assert exchange.MAX_XLSX_IMPORT_BYTES == MAX_XLSX_IMPORT_BYTES
        assert exchange.MAX_FORMULA_LENGTH == MAX_FORMULA_LENGTH
