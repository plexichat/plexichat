"""SEC invariant: every pyotp TOTP/HOTP verify is ASCII-digit canonicalized.

pyotp's verifier NFKC-normalises its input before comparing, so a fullwidth or
other Unicode homoglyph of a code (e.g. ``"１２３４５６"``) verifies for the same
secret as its ASCII spelling. Every replay/single-use guard in this codebase
keys on the *raw* presented code text, so accepting both spellings lets an
already-consumed one-time code mint a second session or authorize a second
privileged action.

All four production verifiers reject non-canonical input at the trust boundary
before calling pyotp:

  * ``src/core/auth/totp.py::verify_totp_code`` uses
    ``_ASCII_DIGITS_RE.fullmatch(code)``;
  * ``src/core/admin/auth/otp_mixin.py::{verify_otp_setup,verify_otp}`` and
    ``src/core/admin/auth/otp_setup_mixin.py::disable_otp`` use
    ``_is_canonical_totp_code(code)``.

This is a pure-AST ratchet over checked-in production source (no app import,
no DB). It enumerates *every* pyotp verify call site under ``src/`` and fails
if any is not inside a function that performs an ASCII-canonical gate. The
detector is self-tested first -- including a decoy whose docstring merely
*mentions* the canonical helper -- so a broken detector cannot pass vacuously.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

# (relative source file, function) pairs known to host a pyotp verifier. Used
# only for the non-vacuity pin; the actual ratchet scans the whole tree.
KNOWN_VERIFIERS = (
    ("src/core/auth/totp.py", "verify_totp_code"),
    ("src/core/admin/auth/otp_mixin.py", "verify_otp_setup"),
    ("src/core/admin/auth/otp_mixin.py", "verify_otp"),
    ("src/core/admin/auth/otp_setup_mixin.py", "disable_otp"),
)


def _is_pyotp_verify(node):
    """Whether ``node`` is a ``<totp|hotp>.verify(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "verify":
        return False
    # Covers ``pyotp.TOTP(secret).verify(...)`` and a locally bound
    # ``totp.verify(...)`` / ``hotp.verify(...)`` receiver alike.
    receiver = ast.unparse(func.value)
    return bool(re.search(r"totp|hotp", receiver, re.IGNORECASE))


def _is_canonical_gate_call(node):
    """Whether ``node`` is one of the sanctioned ASCII-digit gate calls."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return "canonical" in func.id.lower()
    if isinstance(func, ast.Attribute):
        name = func.attr.lower()
        if "canonical" in name:
            return True
        if name in {"fullmatch", "match"}:
            # _ASCII_DIGITS_RE.fullmatch(code)
            if "digit" in ast.unparse(func.value).lower():
                return True
            # re.fullmatch(r"[0-9]{6}", code) and friends
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and ("[0-9]" in arg.value or "\\d" in arg.value)
                ):
                    return True
    return False


def _has_ascii_gate(function):
    """Whether ``function``'s body performs an ASCII-canonical gate."""
    if function is None:
        return False
    return any(_is_canonical_gate_call(node) for node in ast.walk(function))


def _verify_sites(tree):
    """Yield ``(call_node, innermost_function_or_None)`` for pyotp verifies."""
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    for node in ast.walk(tree):
        if not _is_pyotp_verify(node):
            continue
        cur = node
        function = None
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = cur
                break
        yield node, function


def _scan_source(source):
    return list(_verify_sites(ast.parse(textwrap.dedent(source))))


def _scan_tree():
    sites = []
    for path in sorted(SRC.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call, function in _verify_sites(tree):
            sites.append((path.relative_to(ROOT).as_posix(), call, function))
    return sites


_SITES = _scan_tree()


def test_detector_distinguishes_gated_from_ungated_verifiers():
    """Self-test: both gate shapes are recognised; bare/decoy verifies are not."""
    gated_helper = """
    def verify(code):
        if not _is_canonical_totp_code(code):
            return False
        return totp.verify(code, valid_window=1)
    """
    gated_regex = """
    def verify(code):
        if not isinstance(code, str) or not _ASCII_DIGITS_RE.fullmatch(code):
            return False
        return pyotp.TOTP(secret).verify(code)
    """
    gated_inline = """
    def verify(code):
        if re.fullmatch(r"[0-9]{6}", code) is None:
            return False
        return hotp.verify(code)
    """
    ungated = """
    def verify(code):
        return totp.verify(code)
    """
    docstring_decoy = '''
    def verify(code):
        """Uses _is_canonical_totp_code and _ASCII_DIGITS_RE.fullmatch."""
        return totp.verify(code)
    '''
    for source in (gated_helper, gated_regex, gated_inline):
        sites = _scan_source(source)
        assert len(sites) == 1
        assert _has_ascii_gate(sites[0][1])
    for source in (ungated, docstring_decoy):
        sites = _scan_source(source)
        assert len(sites) == 1
        assert not _has_ascii_gate(sites[0][1])


def test_scan_is_not_vacuous():
    """The static scan must actually see all four hardened verifiers."""
    assert len(_SITES) >= 4, f"only {len(_SITES)} pyotp verify sites found"
    seen = set()
    for relative, call, function in _SITES:
        assert function is not None, f"pyotp verify outside a function: {relative}"
        seen.add((relative, function.name))
    for relative, name in KNOWN_VERIFIERS:
        assert (relative, name) in seen, f"missing verifier: {relative}::{name}"


def test_every_pyotp_verify_site_has_ascii_canonical_gate():
    """Fail if any production pyotp verify lacks an ASCII-digit gate."""
    offenders = []
    for relative, call, function in _SITES:
        if not _has_ascii_gate(function):
            offenders.append(f"{relative}:{call.lineno} in {function.name}")
    assert not offenders, (
        "pyotp verifiers without an ASCII-canonical gate (NFKC homoglyph "
        "replay bypass):\n  " + "\n  ".join(sorted(offenders))
    )
