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
if any is not inside a function that performs an ASCII-canonical gate. Both
local-variable aliases and import aliases (``import pyotp as p``,
``from pyotp import TOTP as OTP``) are resolved so an aliased receiver cannot
hide a verify site. The detector is self-tested first -- including a decoy
whose docstring merely *mentions* the canonical helper -- so a broken detector
cannot pass vacuously.
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


_PYOTP_CONSTRUCTORS = frozenset({"totp", "hotp"})


def _import_aliases(tree):
    """Resolve pyotp module/class import aliases across the whole module.

    Local-variable alias resolution (``_pyotp_alias_names``) cannot help when
    the *import itself* is aliased: ``import pyotp as p`` binds ``p`` to the
    module and ``from pyotp import TOTP as OTP`` binds ``OTP`` to a verifier
    class. A verifier named through either spelling carries no ``totp``/``hotp``
    text, so a text-only receiver check silently skips its ``.verify()`` call.
    """
    module_aliases = set()
    constructor_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pyotp" or alias.name.startswith("pyotp."):
                    module_aliases.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "pyotp" or module.startswith("pyotp."):
                for alias in node.names:
                    if alias.name.lower() in _PYOTP_CONSTRUCTORS:
                        constructor_aliases.add(alias.asname or alias.name)
    return module_aliases, constructor_aliases


def _is_pyotp_constructor(
    node, module_aliases=frozenset(), constructor_aliases=frozenset()
):
    """Whether ``node`` builds a pyotp ``TOTP``/``HOTP`` verifier object."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        # ``from pyotp import TOTP`` -> ``TOTP(secret)``, or the same import
        # under an alias: ``from pyotp import TOTP as OTP`` -> ``OTP(secret)``.
        return func.id in constructor_aliases or func.id.lower() in _PYOTP_CONSTRUCTORS
    if isinstance(func, ast.Attribute):
        # ``pyotp.TOTP(secret)`` / ``pyotp.totp.TOTP(secret)``, including an
        # aliased module: ``import pyotp as p`` -> ``p.TOTP(secret)``.
        if func.attr.lower() not in _PYOTP_CONSTRUCTORS:
            return False
        if isinstance(func.value, ast.Name) and func.value.id in module_aliases:
            return True
        return "pyotp" in ast.unparse(func.value).lower()
    return False


def _pyotp_alias_names(
    function, module_aliases=frozenset(), constructor_aliases=frozenset()
):
    """Local names bound to a freshly constructed pyotp verifier object.

    ``verifier = pyotp.TOTP(secret); verifier.verify(code)`` crosses exactly
    the same trust boundary as ``pyotp.TOTP(secret).verify(code)``, but its
    receiver text carries no ``totp``/``hotp`` spelling. Without resolving
    these aliases a text-only scan silently skips such a verify site, so a new
    verifier could reintroduce the NFKC homoglyph replay bypass undetected.
    Import aliases are resolved first so ``v = p.TOTP(...)`` and ``v = OTP(...)``
    register just as the unaliased spellings do.
    """
    names = set()
    if function is None:
        return names
    for node in ast.walk(function):
        value = None
        targets = ()
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign):
            value, targets = node.value, (node.target,)
        if not _is_pyotp_constructor(value, module_aliases, constructor_aliases):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _is_pyotp_verify(
    node, alias_names=frozenset(), module_aliases=frozenset(), constructor_aliases=frozenset()
):
    """Whether ``node`` is a ``<totp|hotp>.verify(...)`` call.

    ``alias_names`` holds local names bound to pyotp verifier objects in the
    enclosing function, so a verifier stored under a non-totp/hotp name is not
    missed. ``module_aliases``/``constructor_aliases`` cover import aliases so
    aliased receivers and inline constructions are recognised too.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "verify":
        return False
    # Covers ``pyotp.TOTP(secret).verify(...)`` and a locally bound
    # ``totp.verify(...)`` / ``hotp.verify(...)`` receiver alike.
    receiver = ast.unparse(func.value)
    if re.search(r"totp|hotp", receiver, re.IGNORECASE):
        return True
    # A verifier cached in a local name that has no totp/hotp spelling.
    if isinstance(func.value, ast.Name) and func.value.id in alias_names:
        return True
    if isinstance(func.value, ast.Attribute) and func.value.attr in alias_names:
        return True
    # Inline construction through an import alias: ``OTP(secret).verify()`` or
    # ``p.TOTP(secret).verify()`` where neither spelling names totp/hotp.
    if _is_pyotp_constructor(func.value, module_aliases, constructor_aliases):
        return True
    return False


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
    module_aliases, constructor_aliases = _import_aliases(tree)
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "verify":
            continue
        cur = node
        function = None
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = cur
                break
        if _is_pyotp_verify(
            node,
            _pyotp_alias_names(function, module_aliases, constructor_aliases),
            module_aliases,
            constructor_aliases,
        ):
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


def test_detector_recognises_pyotp_verify_through_local_alias():
    """A verifier stored under a non-totp/hotp name is still pyotp.

    Non-vacuity: if alias resolution is removed from ``_verify_sites`` /
    ``_is_pyotp_verify`` these sites vanish from the scan and this test fails.
    """
    ungated_alias = """
    def verify(code):
        verifier = pyotp.TOTP(secret)
        return verifier.verify(code)
    """
    gated_alias = """
    def verify(code):
        if not _is_canonical_totp_code(code):
            return False
        verifier = pyotp.TOTP(secret)
        return verifier.verify(code)
    """
    imported_alias = """
    def verify(code):
        v = TOTP(secret)
        return v.verify(code)
    """
    for source in (ungated_alias, imported_alias):
        sites = _scan_source(source)
        assert len(sites) == 1, f"alias verify not detected in: {source}"
        assert not _has_ascii_gate(sites[0][1])
    sites = _scan_source(gated_alias)
    assert len(sites) == 1
    assert _has_ascii_gate(sites[0][1])


def test_detector_recognises_pyotp_verify_through_import_alias():
    """Aliased imports are resolved, so aliased verify sites cannot hide.

    Non-vacuity: before import-alias resolution these three shapes yielded
    zero sites (the receiver text carries no ``totp``/``hotp`` spelling and no
    local alias is registered), so a future verifier written with either import
    form could reintroduce the NFKC homoglyph replay bypass undetected. Removing
    the alias resolution makes these assertions fail.
    """
    module_alias_local = """
    import pyotp as p
    def verify(code):
        v = p.TOTP(secret)
        return v.verify(code)
    """
    class_alias_local = """
    from pyotp import TOTP as OTP
    def verify(code):
        v = OTP(secret)
        return v.verify(code)
    """
    class_alias_inline = """
    from pyotp import TOTP as OTP
    def verify(code):
        return OTP(secret).verify(code)
    """
    gated_module_alias = """
    import pyotp as p
    def verify(code):
        if not _is_canonical_totp_code(code):
            return False
        v = p.TOTP(secret)
        return v.verify(code)
    """
    for source in (module_alias_local, class_alias_local, class_alias_inline):
        sites = _scan_source(source)
        assert len(sites) == 1, f"import-alias verify not detected in: {source}"
        assert not _has_ascii_gate(sites[0][1])
    sites = _scan_source(gated_module_alias)
    assert len(sites) == 1
    assert _has_ascii_gate(sites[0][1])


def test_detector_does_not_flag_non_pyotp_verify_aliases():
    """Alias resolution must not swallow unrelated ``.verify()`` calls."""
    decoy = """
    def check(sig, msg):
        key = Ed25519PublicKey.from_public_bytes(pk)
        return key.verify(sig, msg)
    """
    assert _scan_source(decoy) == []


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
