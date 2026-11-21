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
cannot flake. The detector is first exercised against synthetic verifiers --
including one whose docstring merely *mentions* ``compare_digest`` -- before
the curated real tree is trusted.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Names of the sanctioned constant-time comparators, as either a bare function
# (``constant_time_compare(...)``) or a module attribute
# (``hmac.compare_digest(...)`` / ``secrets.compare_digest(...)``).
CT_COMPARATORS = frozenset({"compare_digest", "constant_time_compare"})

# (relative source file, verifier function) pairs that were hardened to a
# constant-time comparison. Each must still perform at least one such
# comparison, otherwise the ratchet has regressed.
CT_VERIFIERS = (
    ("src/api/__init__.py", "is_self_test_request"),
    ("src/api/routes/auth/oauth.py", "oauth_callback"),
    ("src/core/admin/auth/otp_mixin.py", "verify_otp"),
    ("src/core/applications/manager/webhook.py", "verify_webhook_signature"),
    ("src/core/applications/oauth/tokens.py", "verify_token_hash"),
    ("src/core/applications/oauth/tokens.py", "verify_pkce_challenge"),
    ("src/core/artifacts/federation.py", "verify_payload_signature"),
    ("src/core/auth/deletion_log.py", "_constant_time_hex_equal"),
    ("src/core/auth/managers/twofactor.py", "complete_2fa"),
    ("src/core/auth/oauth/pkce.py", "verify_pkce"),
    ("src/core/auth/oauth/state.py", "verify_oauth_nonce"),
    ("src/core/auth/oauth/state.py", "verify_nonce"),
    ("src/core/auth/tokens.py", "_constant_time_compare"),
    ("src/core/auth/totp.py", "verify_backup_code"),
    ("src/core/dsar/audit_log.py", "_verify_chain_impl"),
    ("src/core/media/security/signing.py", "verify_url"),
    ("src/core/migrations/tracker.py", "_validate_emergency_override"),
    ("src/core/security/kdf.py", "verify_derived_key"),
    ("src/core/security/secrets.py", "constant_time_compare"),
    ("src/core/security/secrets.py", "verify_token"),
    ("src/core/tls/chain_validation.py", "verify_pin"),
    ("src/core/voice/signaling/turn.py", "verify"),
    ("src/core/webhooks/manager/signature.py", "verify_payload"),
    ("src/core/webhooks/manager/token.py", "_verify_token"),
    ("src/static_client/manifest.py", "verify_manifest_detailed"),
)


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes reachable through execution (not definition).

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a nested callable that runs a
    constant-time comparison does not run it. Counting a dead nested comparator
    as protection lets a verifier silence this invariant with dead evidence
    (the same vacuity fixed across the SEC100/200/300 route guards).
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
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in CT_COMPARATORS:
            calls.append(ast.unparse(func))
        elif isinstance(func, ast.Name) and func.id in CT_COMPARATORS:
            calls.append(func.id)
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
    descends into nested functions and lambdas. Merely *defining* a helper or
    lambda that calls a comparator did not run it, so a verifier reintroducing a
    short-circuit ``==`` could silence this invariant with dead evidence. A live
    in-body comparator (including behind statement-level nesting) must count.
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
        fn = _find_function(ast.parse(source), "verify")
        assert _constant_time_calls(fn) == [], (
            "dead nested comparator must not protect the verifier"
        )
    fn = _find_function(ast.parse(live_nested), "verify")
    assert _constant_time_calls(fn), "live in-body comparator must still count"


def test_ct_verifier_scan_is_not_vacuous():
    missing = [f"{rel}:{name}" for rel, name, fn in _SCAN if fn is None]
    assert not missing, f"hardened verifiers dropped out of the scan: {missing}"
    assert len(_SCAN) >= 20, f"only {len(_SCAN)} verifiers enumerated"
    # A few specific ratchets that must be present so a broken scan cannot pass.
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
