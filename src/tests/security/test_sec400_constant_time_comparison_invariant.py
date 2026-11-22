"""SEC400 invariant: secret-verifying call sites use constant-time comparison.

Comparing a presented credential (OTP/backup code, token, signature, nonce,
derived key, ...) with ``==`` or ``in`` short-circuits on the first differing
byte and leaks the stored secret through comparison timing. A series of prior
fixes routed these verifiers through the shared constant-time choke points --
``src.core.security.secrets.constant_time_compare`` (a UTF-8-safe wrapper over
``hmac.compare_digest``) and the direct ``hmac``/``secrets`` comparators. This
suite ratchets those call sites so a future refactor cannot reintroduce a
timing-oracle comparison.

The check is pure AST over checked-in source (no app import, no DB), so it
cannot flake. The set of verifiers to ratchet is *derived from the source
tree*, not maintained as a hand-curated list: every production function that
calls a sanctioned comparator, plus every function that compares a
credential-named parameter with ``==``/``in`` (the shape of a verifier that
forgot the gate), is swept under any name it happens to have. The detector is
first exercised against synthetic verifiers -- including one whose docstring
merely *mentions* ``compare_digest`` -- before the derived real tree is
trusted. A novel-name self-test proves a brand-new verifier cannot escape.
"""

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Names of the sanctioned constant-time comparators, as either a bare function
# (``constant_time_compare(...)``) or a module attribute
# (``hmac.compare_digest(...)`` / ``secrets.compare_digest(...)``).
CT_COMPARATORS = frozenset({"compare_digest", "constant_time_compare"})

# A raw ``==``/``in`` on a parameter with one of these names is the shape of a
# secret verifier that never adopted the constant-time gate. This is the
# independent second anchor that keeps the derived set from being vacuous: a
# reverted verifier drops out of the "calls CT" anchor but lands here.
_CREDENTIAL_PARAM = re.compile(
    r"(?i)(secret|password|passwd|credential|passphrase|api[_-]?key|"
    r"private[_-]?key|digest|signature|nonce|otp|presented|provided)"
)

# Comparison sides that are a format/size check rather than a secret value:
# literals, module constants and len()/int()/str()-style coercions.
_TRIVIAL_COMPARE_SIDE_CALLS = frozenset({"len", "int", "str", "bool", "bytes"})


def _is_ct_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in CT_COMPARATORS
    if isinstance(func, ast.Name):
        return func.id in CT_COMPARATORS
    return False


def _is_trivial_compare_side(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name) and node.id.isupper():
        return True
    if isinstance(node, ast.Attribute) and node.attr.isupper():
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _TRIVIAL_COMPARE_SIDE_CALLS
    ):
        return True
    return False


def _is_raw_credential_compare(node: ast.AST, params: set[str]) -> bool:
    """A timing-oracle ``==``/``in`` of a credential-named parameter."""
    if not isinstance(node, ast.Compare):
        return False
    if not any(isinstance(op, (ast.Eq, ast.In)) for op in node.ops):
        return False
    sides = [node.left] + node.comparators
    credential = [
        side
        for side in sides
        if isinstance(side, ast.Name)
        and side.id in params
        and _CREDENTIAL_PARAM.search(side.id)
    ]
    if not credential:
        return False
    # The first credential-named operand is the presented value; everything
    # else is the comparison target (which may itself be another named secret,
    # e.g. ``presented_secret == stored_secret``).
    other = [side for side in sides if side is not credential[0]]
    if not other or any(_is_trivial_compare_side(side) for side in other):
        return False
    return True


def _derive_from_trees(trees) -> tuple[tuple[str, str], ...]:
    """Derive ``(relative source file, function)`` ratchet pairs from trees.

    ``trees`` is an iterable of ``(relative_path, ast.Module)``. Keeping the
    derivation tree-agnostic lets the self-tests feed it a synthetic module
    with a novel verifier name and prove the sweep discovers it.
    """
    pairs: set[tuple[str, str]] = set()
    for relative, tree in trees:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls_ct = any(_is_ct_call(child) for child in ast.walk(node))
            params = {
                arg.arg
                for arg in (
                    node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                )
            }
            raw_compare = any(
                _is_raw_credential_compare(child, params) for child in ast.walk(node)
            )
            if calls_ct or raw_compare:
                pairs.add((relative, node.name))
    return tuple(sorted(pairs))


def _production_trees():
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if "/tests/" in relative or "/selftest/" in relative:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            # A concurrent edit/delete can disappear a file between rglob and
            # read; the next collection run picks it up.
            continue
        yield relative, tree


def _derive_ct_verifiers() -> tuple[tuple[str, str], ...]:
    """Every production verifier to ratchet, derived structurally."""
    return _derive_from_trees(_production_trees())


# Derived at import time so the whole-tree walk is paid once. Not a curated
# name list: adding a verifier under any new name is picked up automatically.
CT_VERIFIERS = _derive_ct_verifiers()


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes reachable through execution, not definition.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``)
    but skip nested function, async function and lambda bodies: *defining* a
    helper that runs a constant-time comparison does not run it. Counting a
    dead nested comparator as protection lets a verifier silence this invariant
    with dead evidence.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _constant_time_calls(function):
    """Return comparator calls (as source strings) inside ``function``."""
    if function is None:
        return []
    calls = []
    for node in _own_body_nodes(function):
        if isinstance(node, ast.Call) and _is_ct_call(node):
            func = node.func
            calls.append(ast.unparse(func))
    return calls


def _scan():
    results = []
    for relative, name in CT_VERIFIERS:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        function = _find_function(tree, name)
        results.append((relative, name, function))
    return results


_SCAN = _scan()


def test_detector_distinguishes_constant_time_from_short_circuit():
    """Self-test: a real comparator call is seen; ``==`` and a docstring are not."""
    hmac_v = """
    def verify(presented, stored):
        return hmac.compare_digest(presented, stored)
    """
    secrets_v = """
    def verify(presented, stored):
        return secrets.compare_digest(presented, stored)
    """
    shared_v = """
    def verify(presented, stored):
        return constant_time_compare(presented, stored)
    """
    short_circuit = """
    def verify(presented, stored):
        return presented == stored
    """
    # The string appears only in prose -- a grep-based detector would be fooled.
    docstring_decoy = '''
    def verify(presented, stored):
        """Uses hmac.compare_digest to avoid a timing side channel."""
        return presented == stored
    '''
    for source in (hmac_v, secrets_v, shared_v):
        assert _constant_time_calls(
            _find_function(ast.parse(textwrap.dedent(source)), "verify")
        )
    for source in (short_circuit, docstring_decoy):
        assert (
            _constant_time_calls(
                _find_function(ast.parse(textwrap.dedent(source)), "verify")
            )
            == []
        )


def test_plaintext_backup_code_regression_shape_is_detected():
    """Self-test: the exact pre-fix OTP comparison shapes are recognisable."""
    old = """
    def verify(normalized, candidate_sha256, hashed_list, codes):
        for hashed in hashed_list:
            hashed_str = str(hashed)
            if candidate_sha256 == hashed_str:
                return True
        if normalized in codes:
            return True
        return False
    """
    tree = ast.parse(textwrap.dedent(old))
    # Both old shapes are ordinary Compare / membership nodes, not CT calls.
    assert _constant_time_calls(_find_function(tree, "verify")) == []
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    assert any(
        isinstance(n.ops[0], ast.Eq)
        and isinstance(n.left, ast.Name)
        and n.left.id == "candidate_sha256"
        for n in compares
    )
    assert any(isinstance(n.ops[0], ast.In) for n in compares)


def test_dead_nested_constant_time_comparison_does_not_protect_verifier():
    """A comparator inside a never-invoked nested callable is dead evidence.

    Regression: ``_constant_time_calls`` used ``ast.walk(function)``, which
    descends into nested functions and lambdas. A verifier reintroducing a
    short-circuit ``==`` could silence this invariant by parking a comparator
    in code that is defined but never called. Live in-body evidence, including
    behind statement-level nesting, must still count.
    """
    dead_nested = """
    def verify(presented, stored):
        def never_called():
            return hmac.compare_digest(presented, stored)
        return presented == stored
    """
    dead_lambda = """
    def verify(presented, stored):
        _ = lambda: constant_time_compare(presented, stored)
        return presented == stored
    """
    live_nested = """
    def verify(presented, stored):
        for _ in (1,):
            return hmac.compare_digest(presented, stored)
    """
    for source in (dead_nested, dead_lambda):
        fn = _find_function(ast.parse(textwrap.dedent(source)), "verify")
        # Confirm what the old broad traversal would have (wrongly) credited.
        assert any(_is_ct_call(n) for n in ast.walk(fn)), "old logic saw dead evidence"
        assert _constant_time_calls(fn) == [], (
            "dead nested comparator must not protect the verifier"
        )
    fn = _find_function(ast.parse(textwrap.dedent(live_nested)), "verify")
    assert _constant_time_calls(fn), "live in-body comparator must still count"


def test_ct_verifier_scan_is_not_vacuous():
    missing = [f"{rel}:{name}" for rel, name, fn in _SCAN if fn is None]
    assert not missing, f"hardened verifiers dropped out of the scan: {missing}"
    assert len(_SCAN) >= 20, f"only {len(_SCAN)} verifiers enumerated"
    # A few specific ratchets that must be present so a broken derivation
    # cannot pass silently (e.g. if the comparator vocabulary drifts).
    names = {f"{rel}::{name}" for rel, name, _ in _SCAN}
    for pin in (
        "src/core/admin/auth/otp_mixin.py::verify_otp",
        "src/core/auth/totp.py::verify_backup_code",
        "src/core/security/secrets.py::constant_time_compare",
        "src/core/webhooks/manager/signature.py::verify_payload",
    ):
        assert pin in names, pin


def test_every_hardened_verifier_still_compares_in_constant_time():
    offenders = [
        f"{rel}:{name}"
        for rel, name, function in _SCAN
        if not _constant_time_calls(function)
    ]
    assert not offenders, (
        "Secret verifiers without a constant-time comparison:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_admin_otp_backup_codes_never_use_short_circuit_comparison():
    """The d972e9d9 fix must not be undone in the admin backup-code branch."""
    tree = ast.parse(
        (ROOT / "src/core/admin/auth/otp_mixin.py").read_text(encoding="utf-8")
    )
    verify = _find_function(tree, "verify_otp")
    assert verify is not None
    offenders = []
    for node in ast.walk(verify):
        if not isinstance(node, ast.Compare):
            continue
        left = ast.unparse(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and left == "candidate_sha256":
                offenders.append(f"line {node.lineno}: candidate_sha256 == ...")
            if isinstance(op, ast.In) and ast.unparse(comparator) == "codes":
                offenders.append(f"line {node.lineno}: ... in codes")
    assert not offenders, (
        "Admin OTP backup-code comparison regressed to a timing oracle: "
        + "; ".join(sorted(offenders))
    )


def test_novel_named_verifier_is_derived_and_flagged():
    """A new verifier under a name absent from any curated list is swept.

    Regression: CT_VERIFIERS used to be a hand-written tuple of
    ``(file, function)`` pairs. A newly added verifier that compared secrets
    with ``==`` under a novel name was invisible to the guard. The set is now
    derived from source, so the raw comparison is discovered and the
    constant-time ratchet flags it.
    """
    novel = textwrap.dedent(
        """
        def verify_frobnicated_widget(presented_secret, stored_secret):
            if presented_secret == stored_secret:  # timing oracle
                return True
            return False
        """
    )
    relative = "src/core/auth/widgets.py"
    derived = _derive_from_trees([(relative, ast.parse(novel))])
    assert (relative, "verify_frobnicated_widget") in derived

    function = _find_function(ast.parse(novel), "verify_frobnicated_widget")
    assert _constant_time_calls(function) == []
    # The same offenders computation the real-tree ratchet uses must fail.
    offenders = [
        f"{rel}:{name}"
        for rel, name in derived
        if not _constant_time_calls(_find_function(ast.parse(novel), name))
    ]
    assert "src/core/auth/widgets.py:verify_frobnicated_widget" in offenders
