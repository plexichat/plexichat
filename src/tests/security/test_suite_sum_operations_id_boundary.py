"""PlexiSum operations routes must parse IDs at the hardened boundary.

Commit 062a9c42 migrated every PlexiSum route module from bare ``int()`` to
the shared ASCII-only, snowflake-bounded ``parse_id`` gate, but left
``src/api/routes/suite/sum/operations.py`` out of the sweep. That module still
converts string path parameters with ``int()``, which is a parser
differential against the rest of the API:

* ``int("٤٢")`` and ``int("４２")`` accept Unicode digit homoglyphs -> 42;
* ``int("1_0")`` accepts a PEP 515 separator -> 10;
* signs (``"-1"``) and values past the snowflake ceiling are accepted too.

A caller could therefore address another user's workbook/sheet through an ID
the other ten PlexiSum modules reject outright. This suite pins the fix with
an AST check (no app import, config or DB needed) and exercises all four
handlers directly to prove malformed IDs are rejected *before* the manager is
reached, while a real ASCII snowflake still flows through.

pytestmark excludes the perf marker on purpose: this is a deterministic
static/direct-call regression, not a timing-sensitive test.
"""

import ast
import asyncio
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
OPERATIONS = ROOT / "src/api/routes/suite/sum/operations.py"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

# The four handlers in operations.py and the string ID params each accepts.
HANDLERS = {
    "read_viewport": ("workbook_id", "sheet_id"),
    "apply_operation": ("workbook_id", "sheet_id"),
    "sort_range": ("workbook_id", "sheet_id"),
    "replay_operations": ("workbook_id",),
}

# Values that bare int() turns into a number but the shared gate rejects.
MALFORMED_IDS = (
    "٤٢",  # Arabic-Indic digits -> 42
    "４２",  # fullwidth digits -> 42
    "1_0",  # PEP 515 separator -> 10
    "-1",  # negative
    "0",  # below the snowflake floor
    "99999999999999999999",  # past the snowflake ceiling
)


def _is_route_handler(node):
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in HTTP_METHODS
        for dec in node.decorator_list
    )


def _id_params(handler):
    names = set()
    args = handler.args
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        ann = arg.annotation
        is_str = (isinstance(ann, ast.Name) and ann.id == "str") or (
            isinstance(ann, ast.Subscript)
            and isinstance(ann.slice, ast.Name)
            and ann.slice.id == "str"
        )
        if is_str and (arg.arg.endswith("_id") or arg.arg == "id"):
            names.add(arg.arg)
    return names


def _bare_int_violations(tree):
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route_handler(node):
            continue
        params = _id_params(node)
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "int"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
                and sub.args[0].id in params
            ):
                found.append((node.name, sub.args[0].id, sub.lineno))
    return found


_TREE = ast.parse(OPERATIONS.read_text(encoding="utf-8"))
_ROUTE_HANDLERS = [
    node
    for node in ast.walk(_TREE)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and _is_route_handler(node)
]


def test_operations_module_has_no_bare_int_on_string_ids():
    offenders = _bare_int_violations(_TREE)
    assert not offenders, (
        "Bare int() on a PlexiSum string ID route parameter reintroduced:\n  "
        + "\n  ".join(f"{h}:{line} ({param})" for h, param, line in offenders)
    )


def test_scan_is_not_vacuous():
    parse_id_calls = sum(
        1
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "parse_id"
    )
    assert len(_ROUTE_HANDLERS) == len(HANDLERS)
    # One sheet pair per write/viewport handler plus the replay workbook.
    assert parse_id_calls >= 7


def _no_call_manager(called):
    class Manager:
        def __getattr__(self, name):
            def _forbidden(*_args, **_kwargs):
                called.append(name)
                raise AssertionError("manager must not be reached")

            return _forbidden

    return Manager()


def _kwargs(handler_name, workbook_id, sheet_id):
    user = types.SimpleNamespace(user_id=7)
    if handler_name == "read_viewport":
        return dict(
            workbook_id=workbook_id,
            sheet_id=sheet_id,
            row_start=0,
            row_end=50,
            column_start=0,
            column_end=20,
            filters="",
            current_user=user,
        )
    if handler_name == "apply_operation":
        body = types.SimpleNamespace(
            operation_id="op-1",
            operation_type="set_cell",
            base_revision=0,
            cells=[{}],
        )
        return dict(
            workbook_id=workbook_id, sheet_id=sheet_id, body=body, current_user=user
        )
    if handler_name == "sort_range":
        body = types.SimpleNamespace(
            column=0, start_row=0, end_row=5, direction="asc", base_revision=0
        )
        return dict(
            workbook_id=workbook_id, sheet_id=sheet_id, body=body, current_user=user
        )
    return dict(
        workbook_id=workbook_id,
        after_revision=0,
        limit=10,
        current_user=user,
    )


@pytest.mark.parametrize("handler_name", sorted(HANDLERS))
@pytest.mark.parametrize("malformed", MALFORMED_IDS)
@pytest.mark.parametrize("id_param", ["workbook_id", "sheet_id"])
def test_malformed_id_is_rejected_before_manager(
    monkeypatch, handler_name, malformed, id_param
):
    if id_param not in HANDLERS[handler_name]:
        pytest.skip(f"{handler_name} has no {id_param}")

    from src.api.routes.suite import sum as pkg

    called = []
    monkeypatch.setattr(pkg, "_manager", lambda: _no_call_manager(called))
    handler = getattr(pkg, handler_name)

    kwargs = _kwargs(handler_name, "42", "42")
    kwargs[id_param] = malformed

    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(**kwargs))
    assert exc.value.status_code == 400
    assert called == []


@pytest.mark.parametrize("handler_name", sorted(HANDLERS))
def test_valid_id_reaches_manager(monkeypatch, handler_name):
    """Non-vacuity: an ASCII snowflake still flows through as an int."""
    from src.api.routes.suite import sum as pkg

    seen = []

    class Manager:
        def __getattr__(self, name):
            def _record(*args, **_kwargs):
                seen.append((name, args))
                raise RuntimeError("stop here")

            return _record

    monkeypatch.setattr(pkg, "_manager", lambda: Manager())
    handler = getattr(pkg, handler_name)

    kwargs = _kwargs(handler_name, "42", "99")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(**kwargs))
    assert exc.value.status_code == 500
    assert seen
    name, args = seen[0]
    # user_id is first, then the parsed workbook (and sheet) ids as plain ints.
    assert args[0] == 7
    assert args[1] == 42
    if "sheet_id" in HANDLERS[handler_name]:
        assert args[2] == 99
