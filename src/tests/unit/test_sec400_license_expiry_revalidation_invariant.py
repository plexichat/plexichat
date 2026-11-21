"""SEC400 invariant: licence entitlement getters re-check time-based expiry.

A lazy-validated licence caches its verdict on first access. Expiry is
time-dependent, so a verdict that was valid at startup can become stale while a
long-running server stays up. The prior fix (commit 34366773) added
``_needs_revalidation``/``_ensure_validated`` and routed every entitlement
getter and the public validation-result accessor through them, so a lapsed
licence cannot keep granting premium features forever.

This is a pure AST ratchet over the licence manager (no clock, no app import):
the getters must call ``_ensure_validated``, the validator must consult the
expiry check, and none of them may fall back to the old
``if self._validation_result is None: self.validate()`` short circuit. The
detectors are validated against synthetic old/new getters first.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
LICENSING = "src/utils/common_utils/utils/licensing/core.py"
CLASS = "LicenseManager"

# Entitlement getters plus the public validation-result accessor that the fix
# routed through _ensure_validated.
ENTITLEMENT_GETTERS = (
    "has_feature",
    "get_feature_config",
    "get_feature_limit",
    "get_instance_id",
    "is_valid",
)


def _find_class(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _methods(cls):
    return {
        node.name: node
        for node in ast.walk(cls)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _own_body_nodes(node):
    """Nodes in ``node``'s own execution body, skipping nested callables.

    Descend through statement-level nesting but stop at nested functions and
    lambdas: a ``self._ensure_validated()`` call in a helper that is never
    invoked is dead evidence and must not mark a getter as revalidated.
    """
    stack = [node]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            stack.append(child)


def _called_attr(function, name) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        for node in _own_body_nodes(function)
    )


def _has_stale_validate_guard(function) -> bool:
    """True for the old ``if self._validation_result is None: self.validate()``."""
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_none_check = (
            isinstance(test, ast.Compare)
            and any(isinstance(op, (ast.Eq, ast.Is)) for op in test.ops)
            and ast.unparse(test.left) == "self._validation_result"
            and any(
                isinstance(c, ast.Constant) and c.value is None
                for c in test.comparators
            )
        )
        if is_none_check and _called_attr(node, "validate"):
            return True
    return False


_TREE = ast.parse((ROOT / LICENSING).read_text(encoding="utf-8"))
_CLASS = _find_class(_TREE, CLASS)
_METHODS = _methods(_CLASS) if _CLASS is not None else {}


def test_stale_guard_detector_distinguishes_old_and_new_getters():
    """Self-test: the pre-fix pattern is recognised, the hardened one is not."""
    old = textwrap.dedent(
        """
        class M:
            def is_valid(self):
                if self._validation_result is None:
                    self.validate()
                return self._validation_result.is_valid
        """
    )
    new = textwrap.dedent(
        """
        class M:
            def is_valid(self):
                self._ensure_validated()
                return self._validation_result.is_valid
        """
    )
    old_fn = _methods(_find_class(ast.parse(old), "M"))["is_valid"]
    new_fn = _methods(_find_class(ast.parse(new), "M"))["is_valid"]
    assert _has_stale_validate_guard(old_fn)
    assert not _called_attr(old_fn, "_ensure_validated")
    assert not _has_stale_validate_guard(new_fn)
    assert _called_attr(new_fn, "_ensure_validated")


def test_dead_nested_revalidation_does_not_protect_getter():
    """A revalidation call only in a never-invoked helper is not a live check."""
    source = textwrap.dedent(
        """
        class M:
            def is_valid(self):
                def never_called():
                    self._ensure_validated()
                return self._validation_result.is_valid

            def has_feature(self, feature):
                self._ensure_validated()
                return self._validation_result.has(feature)
        """
    )
    methods = _methods(_find_class(ast.parse(source), "M"))
    assert not _called_attr(methods["is_valid"], "_ensure_validated")
    assert _called_attr(methods["has_feature"], "_ensure_validated")


def test_licence_manager_scan_is_not_vacuous():
    assert _CLASS is not None, f"{CLASS} disappeared from {LICENSING}"
    required = ("_needs_revalidation", "_ensure_validated", *ENTITLEMENT_GETTERS)
    missing = [name for name in required if name not in _METHODS]
    assert not missing, f"licence manager methods dropped out of the scan: {missing}"


def test_validator_consult_a_finite_expiry_before_trusting_the_cache():
    needs = _METHODS["_needs_revalidation"]
    ensure = _METHODS["_ensure_validated"]
    needs_src = ast.unparse(needs)
    assert "expires_at" in needs_src, "expiry is no longer considered on revalidation"
    assert "time.time()" in needs_src, "revalidation no longer checks the wall clock"
    assert _called_attr(ensure, "_needs_revalidation"), (
        "_ensure_validated stopped consulting _needs_revalidation"
    )
    assert _called_attr(ensure, "validate"), "_ensure_validated stopped revalidating"


def test_entitlement_getters_revalidate_and_never_use_the_stale_guard():
    stale_or_missing = []
    for name in ENTITLEMENT_GETTERS:
        getter = _METHODS[name]
        if not _called_attr(getter, "_ensure_validated"):
            stale_or_missing.append(f"{name}: no _ensure_validated call")
        if _has_stale_validate_guard(getter):
            stale_or_missing.append(f"{name}: stale 'is None' validate guard")
    assert not stale_or_missing, (
        "Licence entitlement getters can serve a stale 'valid' verdict:\n  "
        + "\n  ".join(sorted(stale_or_missing))
    )
