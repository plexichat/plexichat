"""SEC500 invariant: messaging write paths fail closed on a missing checker.

Several messaging write services used to gate their participation check with
``if participant_svc and ...``. When the messaging module was unwired (or a
manager implementation lacked ``_participant_svc``) the condition short-circuited
and the authorization step was silently skipped, letting any authenticated user
write into a conversation they do not belong to. The fix (voice/scheduled/
last-chat/forwarding) replaced that with an explicit fail-closed guard::

    if not <svc>:
        raise PermissionError("Cannot verify conversation participation")
    if not <svc>.is_participant(conversation_id, user_id):
        raise PermissionError(...)

This suite is a pure-AST ratchet over the messaging services package. It (a)
requires each hardened write method to still carry the explicit fail-closed
guard, and (b) forbids the fail-open ``if <svc> and ...is_participant(...)``
shape anywhere in the service package. It cannot be weakened by a refactor that
merely renames a local variable, because the guard is matched on the resolved
expression.

No app import, config or database is needed. The detector is exercised against
synthetic fail-open / fail-closed / short-circuit-``or`` handlers before the real
tree is scanned, and a non-vacuity test proves the scan sees both the guard and
the participation calls it is meant to protect.
"""

import ast
import os
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = ROOT / "src/core/messaging/services"

FAIL_CLOSED_MESSAGE = "Cannot verify conversation participation"

# (relative source file, write method, resolved checker expression) for every
# write path that was hardened to deny when the participation checker is absent.
FAIL_CLOSED_SURFACES = (
    ("src/core/messaging/services/voice.py", "create_voice_message", "participant_svc"),
    (
        "src/core/messaging/services/forwarding.py",
        "forward_message",
        "self._participant_svc",
    ),
    (
        "src/core/messaging/services/last_chat.py",
        "save_last_chat",
        "self._participant_svc",
    ),
    (
        "src/core/messaging/services/scheduled.py",
        "create_scheduled_message",
        "self._participant_svc",
    ),
)


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _call_attr(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else None


def _has_is_participant(node) -> bool:
    return any(
        isinstance(sub, ast.Call) and _call_attr(sub) == "is_participant"
        for sub in ast.walk(node)
    )


def _is_bare_svc(node) -> bool:
    """True for a bare truthiness operand like ``svc`` or ``self._svc``.

    A negated operand (``not svc``) is an ``UnaryOp`` and deliberately does not
    match, so the fail-closed ``if not svc or ...`` shape is never flagged.
    """
    return isinstance(node, (ast.Name, ast.Attribute)) and ast.unparse(node).endswith(
        "participant_svc"
    )


def _is_fail_open_test(test) -> bool:
    """Recognise ``if <svc> and <...is_participant(...)>:``."""
    if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)):
        return False
    bare_svc = any(_is_bare_svc(value) for value in test.values)
    return bare_svc and _has_is_participant(test)


def _fail_open_violations(tree):
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_fail_open_test(node.test)
    ]


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes reachable through execution (not definition).

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a nested callable that runs
    a guard does not run it. Counting a dead nested guard as protection lets a
    write method silence this invariant with dead evidence (the same vacuity
    fixed across the SEC100/200/300 route guards).
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _raise_messages(node):
    messages = []
    statements = node if isinstance(node, (list, tuple)) else [node]
    for stmt in statements:
        for sub in _own_body_nodes(stmt):
            if not isinstance(sub, ast.Raise) or sub.exc is None:
                continue
            exc = sub.exc
            if isinstance(exc, ast.Call) and exc.args:
                arg = exc.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    messages.append(arg.value)
    return messages


def _has_fail_closed_guard(function, svc_source: str) -> bool:
    """Require ``if not <svc>: raise PermissionError(<sentinel>)`` in the body."""
    for node in _own_body_nodes(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
            continue
        operand = test.operand
        if not isinstance(operand, (ast.Name, ast.Attribute)):
            continue
        if ast.unparse(operand) != svc_source:
            continue
        if any(FAIL_CLOSED_MESSAGE in msg for msg in _raise_messages(node.body)):
            return True
    return False


def _python_files(base: Path):
    for directory, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for filename in sorted(files):
            if filename.endswith(".py"):
                yield Path(directory) / filename


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _scan_tree():
    surfaces = []
    for relative, method, svc in FAIL_CLOSED_SURFACES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        surfaces.append((relative, method, svc, _find_function(tree, method)))

    fail_open = []
    files = 0
    calls = 0
    for path in _python_files(SERVICES_DIR):
        tree = _parse(path)
        if tree is None:
            continue
        files += 1
        calls += sum(1 for node in ast.walk(tree) if _has_is_participant(node))
        for lineno in _fail_open_violations(tree):
            fail_open.append(f"{path.relative_to(ROOT)}:{lineno}")
    return surfaces, fail_open, files, calls


_SURFACES, _FAIL_OPEN, _FILE_COUNT, _CALL_COUNT = _scan_tree()


# ---------------------------------------------------------------------------
# Self-tests: prove the detector fires on the regression and nothing else.
# ---------------------------------------------------------------------------


def test_detector_flags_fail_open_but_accepts_both_fail_closed_shapes():
    fail_open = ast.parse(
        textwrap.dedent(
            """
            class Svc:
                def write(self, user_id, conversation_id):
                    if self._participant_svc and not self._participant_svc.is_participant(
                        conversation_id, user_id
                    ):
                        raise PermissionError("Not a participant")
            """
        )
    )
    explicit = ast.parse(
        textwrap.dedent(
            """
            class Svc:
                def write(self, user_id, conversation_id):
                    if not self._participant_svc:
                        raise PermissionError("Cannot verify conversation participation")
                    if not self._participant_svc.is_participant(conversation_id, user_id):
                        raise PermissionError("Not a participant")
            """
        )
    )
    short_circuit_or = ast.parse(
        textwrap.dedent(
            """
            class Svc:
                def write(self, user_id, conversation_id):
                    if not self._participant_svc or not self._participant_svc.is_participant(
                        conversation_id, user_id
                    ):
                        raise PermissionError("Not a participant")
            """
        )
    )

    assert len(_fail_open_violations(fail_open)) == 1, "fail-open shape was not flagged"
    assert _fail_open_violations(explicit) == [], (
        "explicit guard misflagged as fail-open"
    )
    assert _fail_open_violations(short_circuit_or) == [], "fail-closed 'or' misflagged"

    fn = _find_function(explicit, "write")
    assert _has_fail_closed_guard(fn, "self._participant_svc")
    assert not _has_fail_closed_guard(
        _find_function(fail_open, "write"), "self._participant_svc"
    )


def test_dead_nested_fail_closed_guard_does_not_protect_write_path():
    """A guard in a never-invoked nested callable is dead evidence.

    Regression: ``_has_fail_closed_guard`` used ``ast.walk(function)``, which
    descends into nested functions and lambdas. Merely *defining* a nested
    helper that raises the fail-closed sentinel did not run the guard, so a
    write method stripped of its live guard could silence this invariant with
    dead evidence. A live in-handler guard (including behind statement-level
    nesting) must still count.
    """
    source = textwrap.dedent(
        """
        class Svc:
            def write_dead_nested(self, user_id, conversation_id):
                def never_called():
                    if not self._participant_svc:
                        raise PermissionError("Cannot verify conversation participation")
                return None

            async def write_dead_async_nested(self, user_id, conversation_id):
                async def never_called():
                    if not self._participant_svc:
                        raise PermissionError("Cannot verify conversation participation")
                return None

            def write_live(self, user_id, conversation_id):
                if something:
                    if not self._participant_svc:
                        raise PermissionError("Cannot verify conversation participation")
                return None
        """
    )
    tree = ast.parse(source)
    assert not _has_fail_closed_guard(
        _find_function(tree, "write_dead_nested"), "self._participant_svc"
    ), "dead fail-closed guard in a nested function must not protect the path"
    assert not _has_fail_closed_guard(
        _find_function(tree, "write_dead_async_nested"), "self._participant_svc"
    ), "dead fail-closed guard in a nested async function must not protect the path"
    assert _has_fail_closed_guard(
        _find_function(tree, "write_live"), "self._participant_svc"
    ), "a live in-handler guard behind statement nesting must still protect"


def test_scan_is_not_vacuous():
    missing = [f"{rel}:{method}" for rel, method, _, fn in _SURFACES if fn is None]
    assert not missing, f"hardened write methods dropped out of the scan: {missing}"
    assert len(_SURFACES) == len(FAIL_CLOSED_SURFACES)
    # The package scan really sees both the guards and the calls they protect.
    assert _FILE_COUNT >= 15, f"only {_FILE_COUNT} messaging service files scanned"
    assert _CALL_COUNT >= 20, f"only {_CALL_COUNT} is_participant calls discovered"


def test_every_hardened_write_path_keeps_the_fail_closed_guard():
    offenders = [
        f"{rel}:{method} (checker {svc})"
        for rel, method, svc, fn in _SURFACES
        if not _has_fail_closed_guard(fn, svc)
    ]
    assert not offenders, (
        "Messaging write path lost its fail-closed participation guard:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_no_messaging_service_reintroduces_the_fail_open_check():
    assert not _FAIL_OPEN, (
        "Fail-open participation check (``if svc and ...``) found in messaging "
        "services:\n  " + "\n  ".join(sorted(_FAIL_OPEN))
    )
