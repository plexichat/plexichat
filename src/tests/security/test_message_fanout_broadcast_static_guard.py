"""Static completeness guard: every message-body fan-out strips viewer fields.

Peers fixed the live create/update/tombstone/pin broadcasts so the serialized
``MessageResponse`` copy that goes on the wire is passed through
``_fanout_safe_message_body`` (poll results, read/receipt state, reaction
"me" flags). Runtime tests cover those producers individually; this test is
the *whole-tree* lock that makes the fix complete: any new or edited producer
that feeds a ``model_dump()``/``MessageResponse`` into a message event builder
without the safe transform fails the suite unless it is explicitly listed in
``NON_VIEWER_DATA_FANOUTS`` with a concrete justification.

It reads production source directly (no app import, no DB, no fixture
ordering) so it cannot flake. The set of builders whose payload is a
serialized body is *derived from the signatures* of the canonical builder
module, not hand-curated: a new ``build_message_*`` factory that accepts a
``data`` dict under a novel name is swept automatically instead of silently
escaping the lock. The self-tests at the bottom prove the guard is
non-vacuous: swapping a real ``_fanout_safe_message_body(response)`` call for
``response.model_dump()`` is detected, as is a brand-new body builder that was
never added to any curated name list.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

# The canonical message-event builder module. Every producer of live message
# events is required to build through it, so the builder surface can be derived
# from its function signatures rather than maintained as a curated name list.
BUILDER_SRC = ROOT / "src/core/events/message_events.py"

SAFE_TRANSFORM = "_fanout_safe_message_body"


def _regular_params(fn):
    """Positional-or-keyword parameter names (excludes ``*args``/``**kwargs``)."""
    return [arg.arg for arg in fn.args.posonlyargs + fn.args.args]


def _body_builders(tree):
    """``build_message_*`` factories that accept a serialized body dict.

    A factory whose regular parameters include ``data`` can carry a
    ``MessageResponse``/``model_dump()`` payload, so its fan-out MUST pass
    through the safe transform. Deriving this from signatures means a newly
    added body builder is tracked under any name without editing this guard.
    """
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("build_message_")
        and "data" in _regular_params(node)
    }


def _tombstone_builders(tree):
    """ID-only ``build_message_*`` factories (first regular arg is an id).

    They carry only id/channel_id (+ optional marker), so there is no
    viewer-scoped body -- but we still assert nothing viewer-shaped is smuggled
    through ``extra``.
    """
    result = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("build_message_"):
            continue
        params = _regular_params(node)
        if params and params[0].endswith("_id") and "data" not in params:
            result.add(node.name)
    return result


_BUILDER_TREE = ast.parse(BUILDER_SRC.read_text(encoding="utf-8"))
BODY_BUILDERS = frozenset(_body_builders(_BUILDER_TREE))
TOMBSTONE_BUILDERS = frozenset(_tombstone_builders(_BUILDER_TREE))
ALL_BUILDERS = BODY_BUILDERS | TOMBSTONE_BUILDERS

# (relative source path, builder, unparsed data argument) -> justification.
# Only events that provably carry NO viewer-specific message data belong here.
NON_VIEWER_DATA_FANOUTS = {
    (
        "src/core/media/_scanning.py",
        "build_message_update",
        "event_data",
    ): (
        "hand-rolled minimal dict (id/content/remaining-attachments only); "
        "no poll, read/receipt, status or reaction fields are ever added to "
        "this payload, so there is no viewer window to sanitize"
    ),
    (
        "src/core/events/message_events.py",
        "build_message_create",
        "data",
    ): (
        "build_flat_message_create assembles ``data`` from explicit scalar "
        "kwargs (id/author/content/...); no MessageResponse or model_dump "
        "passes through this internal factory"
    ),
}


class Site:
    """One message-event-builder call site found in production source."""

    __slots__ = ("path", "builder", "data_source", "kind")

    def __init__(self, path, builder, data_source, kind):
        self.path = path
        self.builder = builder
        self.data_source = data_source
        self.kind = kind

    @property
    def key(self):
        return (self.path, self.builder, self.data_source)

    def __repr__(self):
        return f"Site({self.path}, {self.builder}, {self.data_source!r}, {self.kind})"


def _canonical_names(tree: ast.AST, tracked: frozenset[str]) -> dict[str, str]:
    """Resolve local aliases of any name in ``tracked`` to its canonical name.

    A producer that imports ``build_message_update as bmu`` or binds
    ``bmu = build_message_update`` is still calling the very same fan-out
    factory; identifying call sites by the bare name at the call would let
    such a producer slip past the whole-tree lock. Module attribute access
    (``events.build_message_update(...)``) already yields the canonical attr
    name from :func:`_callee_name` and needs no mapping.

    Resolution runs to a fixpoint so chained aliases (``import as bmu;
    emit = bmu``) collapse to the canonical name too. Over-resolving a name
    is safe here: it can only make the guard flag *more* sites, never fewer.
    """
    aliases: dict[str, str] = {}

    def resolve(name: str) -> str:
        seen: set[str] = set()
        while name in aliases and name not in seen:
            seen.add(name)
            name = aliases[name]
        return name

    nodes = list(ast.walk(tree))
    for _ in range(len(nodes) + 1):
        changed = False
        for node in nodes:
            pairs: list[tuple[str, str]] = []
            if isinstance(node, ast.ImportFrom):
                pairs = [
                    (alias.asname or alias.name, alias.name)
                    for alias in node.names
                    if alias.name in tracked
                ]
            elif isinstance(node, ast.Assign):
                value = node.value
                canonical = None
                if isinstance(value, ast.Name):
                    canonical = resolve(value.id)
                elif isinstance(value, ast.Attribute):
                    canonical = resolve(value.attr)
                if canonical in tracked:
                    pairs = [
                        (target.id, canonical)
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    ]
            for local, canonical in pairs:
                if aliases.get(local) != canonical:
                    aliases[local] = canonical
                    changed = True
        if not changed:
            break
    return aliases


def _callee_name(call: ast.Call, aliases: dict[str, str]) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return ""
    return aliases.get(name, name)


def _is_safe_transform(expr: ast.AST, aliases: dict[str, str]) -> bool:
    return isinstance(expr, ast.Call) and _callee_name(expr, aliases) == SAFE_TRANSFORM


def _is_model_dump(expr: ast.AST) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "model_dump"
    )


def _data_arg(
    call: ast.Call, builder: str, tombstones: frozenset[str]
) -> ast.AST | None:
    if builder in tombstones:
        return None
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "data":
            return kw.value
    return None


def _scan_source(
    source: str,
    rel_path: str,
    *,
    builders: frozenset[str] = ALL_BUILDERS,
    tombstones: frozenset[str] = TOMBSTONE_BUILDERS,
) -> list[Site]:
    """Classify every message-event-builder call in ``source``.

    ``builders``/``tombstones`` default to the sets derived from the canonical
    builder module; the self-test passes a synthetic set to prove a brand-new
    builder name is swept too.
    """
    tree = ast.parse(source)
    aliases = _canonical_names(tree, builders | tombstones | {SAFE_TRANSFORM})
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node, aliases)
        if name not in builders:
            continue

        if name in tombstones:
            # ID-only tombstone. The only way viewer data could sneak in is
            # an explicit ``extra={...}`` carrying a raw model_dump.
            smuggled = any(
                _is_model_dump(kw.value) for kw in node.keywords if kw.arg == "extra"
            )
            sites.append(
                Site(
                    rel_path,
                    name,
                    "<id-only-tombstone>",
                    "raw_dump_in_extra" if smuggled else "no_body",
                )
            )
            continue

        data = _data_arg(node, name, tombstones)
        if data is None:
            sites.append(Site(rel_path, name, "<missing>", "missing_data"))
            continue
        source_text = " ".join(ast.unparse(data).split())
        if _is_safe_transform(data, aliases):
            kind = "fanout_safe"
        else:
            # A raw model_dump, a bare variable holding one, a dict literal,
            # etc. -- all require an explicit justification in the allowlist.
            kind = "unprotected"
        sites.append(Site(rel_path, name, source_text, kind))
    return sites


def _iter_production_sites():
    for path in sorted(SRC.rglob("*.py")):
        rel_parts = path.relative_to(SRC).parts
        if "tests" in rel_parts or "__pycache__" in rel_parts:
            continue
        rel_path = str(path.relative_to(ROOT))
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for site in _scan_source(source, rel_path):
            yield site


_SITES = list(_iter_production_sites())


def _offenders(sites):
    return [
        s
        for s in sites
        if s.kind not in {"fanout_safe", "no_body"}
        and s.key not in NON_VIEWER_DATA_FANOUTS
    ]


def test_scan_is_not_vacuous():
    """The scan really covers every message-event producer in the tree."""
    assert len(_SITES) >= 9, f"only discovered {len(_SITES)} builder call sites"
    assert sum(1 for s in _SITES if s.kind == "fanout_safe") >= 4, (
        "expected the create/update/pin/tombstone producers to use the safe transform"
    )
    covered = {s.path for s in _SITES}
    for path in (
        "src/api/routes/messages/messages_crud/broadcast.py",
        "src/api/routes/messages/pins.py",
        "src/core/automod/actions/delete.py",
    ):
        assert path in covered, f"producer not scanned: {path}"


def test_every_message_body_fanout_is_safe_or_allowlisted():
    bad = _offenders(_SITES)
    assert not bad, (
        "Message-event fan-outs that carry a serialized body without "
        "_fanout_safe_message_body:\n  "
        + "\n  ".join(f"{s.path}: {s.builder}({s.data_source})" for s in bad)
    )


def test_allowlist_has_no_stale_entries():
    present = {s.key for s in _SITES}
    stale = sorted(set(NON_VIEWER_DATA_FANOUTS) - present)
    assert not stale, (
        "Stale NON_VIEWER_DATA_FANOUTS entries (site changed or was removed); "
        "update or delete them:\n  "
        + "\n  ".join(f"{p}: {b}({d})" for p, b, d in stale)
    )


def test_allowlist_entries_are_concrete():
    for (path, builder, data_source), reason in NON_VIEWER_DATA_FANOUTS.items():
        assert path.startswith("src/"), path
        assert builder in ALL_BUILDERS, builder
        assert data_source.strip(), data_source
        assert len(reason.strip()) > 20, reason


def test_safe_transform_form_is_recognized():
    sites = _scan_source(
        "build_message_update(_fanout_safe_message_body(response), channel_id=1)",
        "src/any.py",
    )
    assert len(sites) == 1
    assert sites[0].kind == "fanout_safe"
    assert not _offenders(sites)


def test_raw_model_dump_is_flagged():
    sites = _scan_source(
        "build_message_update(response.model_dump(), channel_id=1)",
        "src/any.py",
    )
    assert len(sites) == 1
    assert sites[0].kind == "unprotected"
    assert _offenders(sites), "a raw model_dump fan-out must be flagged"


def test_aliased_builder_import_with_raw_dump_is_flagged():
    """``import ... as`` on the factory must not hide a raw model_dump."""
    source = "\n".join(
        (
            "from src.core.events import build_message_update as bmu",
            "",
            "def send(response):",
            "    return bmu(response.model_dump(), channel_id=1)",
        )
    )
    sites = _scan_source(source, "src/any.py")
    assert len(sites) == 1
    assert sites[0].builder == "build_message_update"
    assert sites[0].kind == "unprotected"
    assert _offenders(sites), "raw model_dump via aliased builder import was missed"


def test_assignment_alias_of_builder_with_raw_dump_is_flagged():
    """A plain assignment alias of the factory is still the factory."""
    source = "\n".join(
        (
            "bmu = build_message_update",
            "",
            "def send(response):",
            "    return bmu(response.model_dump(), channel_id=1)",
        )
    )
    sites = _scan_source(source, "src/any.py")
    assert len(sites) == 1
    assert sites[0].builder == "build_message_update"
    assert sites[0].kind == "unprotected"
    assert _offenders(sites), "raw model_dump via assignment alias was missed"


def test_aliased_safe_transform_is_recognized():
    """A renamed safe transform must not create a false positive."""
    source = "\n".join(
        (
            "from src.api.routes.messages.messages_crud.broadcast import (",
            "    _fanout_safe_message_body as safe,",
            ")",
            "",
            "def send(response):",
            "    return build_message_update(safe(response), channel_id=1)",
        )
    )
    sites = _scan_source(source, "src/any.py")
    assert len(sites) == 1
    assert sites[0].builder == "build_message_update"
    assert sites[0].kind == "fanout_safe"
    assert not _offenders(sites)


def test_swapping_safe_transform_for_raw_dump_in_real_source_is_flagged():
    """Non-vacuity against the actual producer source, not just a snippet."""
    path = ROOT / "src/api/routes/messages/messages_crud/broadcast.py"
    source = path.read_text(encoding="utf-8")
    # Negative lookbehind keeps the ``def _fanout_safe_message_body(...)``
    # declaration intact; only the two inline call sites are swapped.
    call_pattern = re.compile(r"(?<!def )_fanout_safe_message_body\(response\)")
    assert len(call_pattern.findall(source)) >= 1, (
        "guard premise changed: safe call no longer inline"
    )

    swapped = call_pattern.sub("response.model_dump()", source)
    swapped_sites = _scan_source(
        swapped, "src/api/routes/messages/messages_crud/broadcast.py"
    )
    assert _offenders(swapped_sites), (
        "replacing _fanout_safe_message_body(response) with "
        "response.model_dump() in broadcast.py was NOT flagged"
    )


def test_body_builders_are_derived_not_hand_curated():
    """Non-vacuity pin: the derived sets must match the reviewed surface.

    If the canonical builder module is renamed wholesale, or a body factory
    stops accepting ``data``, this pin fails instead of the sweep quietly
    becoming vacuous.
    """
    assert {"build_message_create", "build_message_update"} <= BODY_BUILDERS
    assert "build_message_delete" in TOMBSTONE_BUILDERS
    assert not (BODY_BUILDERS & TOMBSTONE_BUILDERS)
    assert "build_flat_message_create" not in ALL_BUILDERS, (
        "the flat factory assembles explicit scalars, not a serialized body"
    )


def test_novel_named_body_builder_is_discovered_and_swept():
    """A new body builder under a novel name cannot escape the whole-tree lock.

    Regression: BODY_BUILDERS used to be a hand-curated frozenset. Adding
    ``build_message_edit(data, ...)`` to the canonical builder module and
    fanning it out with ``response.model_dump()`` was invisible to the guard.
    The builder set is now derived from the module's signatures, so the novel
    builder is discovered and its raw-dump fan-out is flagged.
    """
    novel_module = textwrap.dedent(
        """
        def build_message_create(data, *, channel_id): ...
        def build_message_update(data, *, channel_id): ...
        def build_message_edit(data, *, channel_id): ...
        def build_message_delete(message_id, channel_id): ...
        """
    )
    derived = _body_builders(ast.parse(novel_module))
    assert derived == {
        "build_message_create",
        "build_message_update",
        "build_message_edit",
    }

    producer = "build_message_edit(response.model_dump(), channel_id=1)"
    sites = _scan_source(producer, "src/any.py", builders=derived)
    assert len(sites) == 1
    assert sites[0].kind == "unprotected"
    assert _offenders(sites), "raw dump into a novel body builder was not flagged"
