"""SEC500 invariant: the shared outbound URL gate keeps its SSRF defenses.

``src.core.security.outbound`` is the single choke point for URLs the server
persists or fetches. Over several fixes it grew a set of *cheap structural*
rejections that run before DNS resolution and before any downstream client can
re-resolve the name: control characters, an http(s) scheme allowlist, embedded
credentials, and a backslash in the authority (a urlparse-vs-WHATWG parser
differential that smuggles a link-local address into the userinfo). It then
delegates the resolved-IP check to ``URLValidator`` and returns the resolved
address alongside the host so callers connect by address, defeating rebinding.

This suite is a pure-AST ratchet over that module and its adoption sites: the
structural checks and the single-gate delegation must remain, and the schema
validators that accept user-supplied URLs must keep using the gate rather than a
raw ``URLValidator`` copy. No app import, config, DB or network is needed.

The detector is validated against synthetic complete/regressed gates and a
raw-validator adoption module before the real tree is trusted, and a non-vacuity
test proves the scan sees the real structural markers.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
GATE = "src/core/security/outbound.py"

# Schema/validation modules that accept attacker-controlled URLs. Each must
# route them through the vetted gate, never a raw URLValidator copy.
GATE_ADOPTERS = (
    ("src/api/schemas/media.py", "resolve_outbound_url"),
    ("src/api/schemas/servers.py", "resolve_outbound_url"),
    ("src/core/servers/validation.py", "parse_outbound_url"),
)

EXPECTED_SCHEMES = frozenset({"http", "https"})


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _own_body_nodes(function):
    """Nodes in ``function``'s own execution body, skipping nested callables.

    Merely defining a nested function or lambda that performs a structural
    check does not run it. Crediting those dead checks as present lets a gate
    whose body does nothing silence this ratchet with evidence that never
    executes (the same vacuity fixed in the sibling route guards).
    """
    stack = [function]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            stack.append(child)


def _call_attrs(function):
    return {
        node.func.attr
        for node in _own_body_nodes(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _call_names(function):
    return {
        node.func.id
        for node in _own_body_nodes(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _loaded_names(function):
    return {node.id for node in _own_body_nodes(function) if isinstance(node, ast.Name)}


def _attribute_names(function):
    return {
        node.attr
        for node in _own_body_nodes(function)
        if isinstance(node, ast.Attribute)
    }


def _string_literals(function):
    return {
        node.value
        for node in _own_body_nodes(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _allowed_schemes(tree):
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(
            isinstance(t, ast.Name) and t.id == "ALLOWED_SCHEMES" for t in targets
        ):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args
            and isinstance(value.args[0], (ast.Set, ast.List, ast.Tuple))
        ):
            return frozenset(
                e.value
                for e in value.args[0].elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
    return None


def _backslash_check_missing(fn) -> list[str]:
    """Missing markers in the authority-backslash detector."""
    missing = []
    if "\\" not in _string_literals(fn):
        missing.append("backslash literal")
    if "netloc" not in _attribute_names(fn):
        missing.append("parsed.netloc")
    return missing


def _gate_checks_missing(fn) -> list[str]:
    """Missing structural checks / delegation in ``parse_outbound_url``."""
    missing = []
    names = _call_names(fn)
    attrs = _call_attrs(fn)
    attr_refs = _attribute_names(fn)
    loaded = _loaded_names(fn)
    for label, present in (
        ("control-char rejection", "reject_control_characters" in names),
        ("backslash-authority rejection", "_authority_has_backslash" in names),
        ("scheme allowlist reference", "ALLOWED_SCHEMES" in loaded),
        ("scheme parse", "scheme" in attr_refs),
        ("credentials rejection", "username" in attr_refs and "password" in attr_refs),
        ("resolved-IP SSRF check", "validate_url_for_request" in attrs),
        ("typed rejection error", "OutboundUrlRejected" in names),
    ):
        if not present:
            missing.append(label)
    return missing


def _resolver_missing(fn) -> list[str]:
    return (
        [] if "parse_outbound_url" in _call_names(fn) else ["parse_outbound_url call"]
    )


def _ip_literal_missing(fn) -> list[str]:
    missing = []
    attrs = _call_attrs(fn)
    if "ip_address" not in attrs:
        missing.append("ip_address")
    if "is_private_ip" not in attrs:
        missing.append("is_private_ip")
    return missing


def _parse(path: Path):
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


_GATE_TREE = _parse(GATE)
_ADOPTER_SCAN = []
for _rel, _helper in GATE_ADOPTERS:
    _tree = _parse(_rel)
    _imports = {
        alias.asname or alias.name
        for node in ast.walk(_tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "src.core.security.outbound"
        for alias in node.names
    }
    _raw_validator = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "validate_url_for_request"
        for node in ast.walk(_tree)
    )
    _ADOPTER_SCAN.append((_rel, _helper, _tree, _imports, _raw_validator))


# ---------------------------------------------------------------------------
# Self-tests: prove the detectors fire on regressions and not on good code.
# ---------------------------------------------------------------------------


def test_gate_detector_flags_each_removed_structural_check():
    complete = ast.parse(
        textwrap.dedent(
            """
            def parse_outbound_url(value, field="url"):
                candidate = reject_control_characters(value, field).strip()
                parsed = urlparse(candidate)
                scheme = parsed.scheme.lower()
                if scheme not in ALLOWED_SCHEMES:
                    raise OutboundUrlRejected(field)
                if parsed.username or parsed.password:
                    raise OutboundUrlRejected(field)
                if _authority_has_backslash(candidate):
                    raise OutboundUrlRejected(field)
                host, address = URLValidator().validate_url_for_request(candidate)
                return OutboundUrl(candidate, host, address)
            """
        )
    )
    good_fn = _find_function(complete, "parse_outbound_url")
    assert _gate_checks_missing(good_fn) == []

    regress = ast.parse(
        textwrap.dedent(
            """
            def parse_outbound_url(value, field="url"):
                parsed = urlparse(value)
                if parsed.scheme not in ALLOWED_SCHEMES:
                    raise OutboundUrlRejected(field)
                host, address = URLValidator().validate_url_for_request(value)
                return OutboundUrl(value, host, address)
            """
        )
    )
    missing = _gate_checks_missing(_find_function(regress, "parse_outbound_url"))
    for label in (
        "control-char rejection",
        "backslash-authority rejection",
        "credentials rejection",
    ):
        assert label in missing, f"regression '{label}' was not flagged"

    good_backslash = ast.parse(
        'def _authority_has_backslash(url):\n    return "\\\\" in (urlparse(url).netloc or "")\n'
    )
    bad_backslash = ast.parse(
        'def _authority_has_backslash(url):\n    return "\\n" in url\n'
    )
    assert (
        _backslash_check_missing(
            _find_function(good_backslash, "_authority_has_backslash")
        )
        == []
    )
    assert _backslash_check_missing(
        _find_function(bad_backslash, "_authority_has_backslash")
    )


def test_gate_detector_rejects_dead_nested_checks():
    """Structural checks in a never-called nested helper are not a live gate."""
    dead = ast.parse(
        textwrap.dedent(
            """
            def parse_outbound_url(value, field="url"):
                def never_called():
                    candidate = reject_control_characters(value, field).strip()
                    parsed = urlparse(candidate)
                    scheme = parsed.scheme.lower()
                    if scheme not in ALLOWED_SCHEMES:
                        raise OutboundUrlRejected(field)
                    if parsed.username or parsed.password:
                        raise OutboundUrlRejected(field)
                    if _authority_has_backslash(candidate):
                        raise OutboundUrlRejected(field)
                    host, address = URLValidator().validate_url_for_request(candidate)
                    return OutboundUrl(candidate, host, address)
                return urlparse(value)
            """
        )
    )
    fn = _find_function(dead, "parse_outbound_url")
    assert _gate_checks_missing(fn), "dead nested checks must not satisfy the gate"


def test_adoption_detector_distinguishes_gate_from_raw_validator():
    good = ast.parse(
        textwrap.dedent(
            """
            from src.core.security.outbound import resolve_outbound_url

            def check(value):
                return resolve_outbound_url(value, "Attachment URL")
            """
        )
    )
    regress = ast.parse(
        textwrap.dedent(
            """
            from src.utils.security import URLValidator

            def check(value):
                return URLValidator().validate_url_for_request(value)
            """
        )
    )

    def _uses_raw_validator(tree):
        return any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "validate_url_for_request"
            for node in ast.walk(tree)
        )

    assert not _uses_raw_validator(good)
    assert _uses_raw_validator(regress)
    assert "resolve_outbound_url" in {
        alias.name
        for node in ast.walk(good)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }


def test_gate_scan_is_not_vacuous():
    for name in (
        "parse_outbound_url",
        "resolve_outbound_url",
        "_authority_has_backslash",
        "reject_forbidden_ip_literal",
    ):
        assert _find_function(_GATE_TREE, name) is not None, f"{name} missing from gate"
    assert _allowed_schemes(_GATE_TREE) == EXPECTED_SCHEMES
    # A broken detector that never fires would pass the "missing == []" tests
    # trivially; prove it really sees the structural markers on the real tree.
    gate_fn = _find_function(_GATE_TREE, "parse_outbound_url")
    assert len(_gate_checks_missing(gate_fn)) == 0
    assert "validate_url_for_request" in _call_attrs(gate_fn)
    assert "_authority_has_backslash" in _call_names(gate_fn)
    backslash_fn = _find_function(_GATE_TREE, "_authority_has_backslash")
    assert _backslash_check_missing(backslash_fn) == []
    assert len(_ADOPTER_SCAN) == len(GATE_ADOPTERS)
    assert all(helper in imports for _, helper, _, imports, _ in _ADOPTER_SCAN)


# ---------------------------------------------------------------------------
# Real-tree invariants.
# ---------------------------------------------------------------------------


def test_outbound_gate_retains_all_structural_ssrf_checks():
    offenders = []
    backslash_fn = _find_function(_GATE_TREE, "_authority_has_backslash")
    if backslash_fn is None:
        offenders.append("gate: _authority_has_backslash missing")
    else:
        offenders += [f"gate: {m}" for m in _backslash_check_missing(backslash_fn)]

    gate_fn = _find_function(_GATE_TREE, "parse_outbound_url")
    if gate_fn is None:
        offenders.append("gate: parse_outbound_url missing")
    else:
        offenders += [f"gate: {m}" for m in _gate_checks_missing(gate_fn)]

    resolver_fn = _find_function(_GATE_TREE, "resolve_outbound_url")
    if resolver_fn is None:
        offenders.append("gate: resolve_outbound_url missing")
    else:
        offenders += [f"gate: {m}" for m in _resolver_missing(resolver_fn)]

    ip_fn = _find_function(_GATE_TREE, "reject_forbidden_ip_literal")
    if ip_fn is None:
        offenders.append("gate: reject_forbidden_ip_literal missing")
    else:
        offenders += [f"gate: {m}" for m in _ip_literal_missing(ip_fn)]

    if _allowed_schemes(_GATE_TREE) != EXPECTED_SCHEMES:
        offenders.append("gate: http(s) scheme allowlist changed")

    assert not offenders, (
        "Outbound URL gate lost a structural SSRF defense:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_url_adopters_route_through_the_vetted_gate():
    offenders = []
    for rel, helper, tree, imports, raw_validator in _ADOPTER_SCAN:
        if helper not in imports:
            offenders.append(f"{rel}: no longer imports {helper}")
        if not any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == helper)
                or (isinstance(node.func, ast.Attribute) and node.func.attr == helper)
            )
            for node in ast.walk(tree)
        ):
            offenders.append(f"{rel}: no longer calls {helper}")
        if raw_validator:
            offenders.append(f"{rel}: raw URLValidator.validate_url_for_request call")
    assert not offenders, (
        "URL adopter bypassed the vetted outbound gate:\n  "
        + "\n  ".join(sorted(offenders))
    )
