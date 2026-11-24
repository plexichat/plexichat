"""Structural guard: serialization-size caps must measure the persisted form.

Follow-up to two representation-mismatch fixes in the serialization-size
campaign:

* ``ae6b9936`` (interactions): the payload cap measured the *compact*
  ``json.dumps(data, separators=(",", ":"))`` form while ``create_interaction``
  persists/encrypts the *default* ``json.dumps(data)`` form.  A payload just
  under the cap in compact form stored well over it.  Fixed by measuring the
  persisted representation.
* ``693f4c2a`` (artifact op-log): the byte cap measured
  ``json.dumps(data, ensure_ascii=False)`` while ``_json_dumps`` persists the
  default ``ensure_ascii=True`` form (non-ASCII escaped to ``\\uXXXX``).  Fixed
  by measuring ``_json_dumps(data)`` itself.

Runtime tests cover the individual caps, but nothing stops a later edit from
reintroducing a compact measurement in front of a default-form sink, adding a
brand-new size check whose measurement disagrees with its persist form, or
drifting one of the already-fixed sites.  This is a pure AST ratchet (no app
import / config / DB) over COMMITTED HEAD blobs:

1. ``_find_measurements`` enumerates every production site that calls
   ``json.dumps`` / ``_json_dumps`` (optionally ``.encode()``) purely to feed a
   ``len(...)`` size/quota check -- both the direct
   ``len(json.dumps(x, ...))`` form and the common
   ``s = json.dumps(x); len(s)`` / ``len(s.encode())`` intermediate form.
2. ``SITE_REGISTRY`` documents all such sites found in HEAD.  Each entry pins
   the *exact* serialization kwargs (drift detection) and the form of the
   representation actually persisted / transmitted.  For persist-bound sites
   the guard asserts the two forms MATCH by extracting the sink's kwargs from
   HEAD, not by trusting a literal.  Non-default forms are allowlisted only
   with a written reason (compact-canonical webhook/push/suite encodings and
   the pretty-printed DSAR export, plus two gateway transport heuristics that
   are explicitly not storage caps).
3. The found set must equal the registry: a NEW size check (or a removed one)
   fails the sweep, so a reviewer must register and justify it.
4. Synthetic self-tests prove the detectors are not vacuous: a compact
   measurement against a default persist is flagged; matched forms are
   accepted; the detector really extracts ``ensure_ascii=False``; a new site
   is flagged; and the committed-tree reader really sweeps HEAD.

Blobs are read from COMMITTED HEAD (``git show HEAD:<path>``), not the working
tree: many production files are contested by parallel agents whose stale copies
can mislead a working-tree scan while a whole-file commit could silently revert
a fix.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import pytest

# Committed-tree sweep: ~5.5k HEAD blobs per test (20-30s here), so the
# suite-wide per-test timeout is too tight on a loaded runner. Assertions are
# unchanged; only the wall-clock budget.
pytestmark = [
    pytest.mark.security,
    pytest.mark.unit,
    pytest.mark.timeout(180),
]

ROOT = Path(__file__).resolve().parents[3]

# Bare helper names that serialize a value to the string that is persisted.
SERIALIZER_NAMES = frozenset({"_json_dumps", "api_json_dumps"})


# ---------------------------------------------------------------------------
# Git / committed-tree plumbing
# ---------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run and pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str) -> Callable[[str], str]:
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


_EXCLUDED_COMPONENTS = frozenset({"tests", "selftest", "test"})


def _is_excluded(path: str) -> bool:
    return any(part in _EXCLUDED_COMPONENTS for part in path.split("/")[:-1])


@lru_cache(maxsize=None)
def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr.strip()}"
    files = tuple(
        p for p in proc.stdout.splitlines() if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


# ---------------------------------------------------------------------------
# AST detection of serialization-size measurements
# ---------------------------------------------------------------------------


def _is_serializer(call: ast.Call) -> bool:
    """True for ``json.dumps(...)`` / ``_json_dumps(...)`` style calls."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "dumps":
        return isinstance(func.value, ast.Name) and func.value.id == "json"
    if isinstance(func, ast.Name) and func.id in SERIALIZER_NAMES:
        return True
    return False


def _semantic_kwargs(call: ast.Call) -> tuple[tuple[str, str], ...]:
    """Normalize a serializer call's kwargs into a stable, comparable tuple.

    Separators are reduced to ``"compact"`` for ``(",", ":")`` so whitespace in
    source formatting cannot create false drift.  ``default=str`` is kept for
    pinning but is deliberately ignored by :func:`_form` (it cannot change the
    bytes of a value that is already JSON-serializable).
    """
    out: dict[str, str] = {}
    for kw in call.keywords:
        if kw.arg is None:  # **kwargs -- pin verbatim, ignore in form
            out["**"] = ast.unparse(kw.value)
            continue
        value = kw.value
        if (
            kw.arg == "separators"
            and isinstance(value, ast.Tuple)
            and len(value.elts) == 2
            and all(
                isinstance(e, ast.Constant) and isinstance(e.value, str)
                for e in value.elts
            )
        ):
            first, second = value.elts[0].value, value.elts[1].value
            out["separators"] = (
                "compact"
                if (first == "," and second == ":")
                else f"{first!r}/{second!r}"
            )
        elif isinstance(value, ast.Constant):
            out[kw.arg] = str(value.value)
        else:
            out[kw.arg] = ast.unparse(value)
    return tuple(sorted(out.items()))


def _form(kwargs: tuple[tuple[str, str], ...]) -> frozenset[tuple[str, str]]:
    """The size-affecting serialization form of normalized kwargs."""
    kw = dict(kwargs)
    return frozenset(
        {
            ("ensure_ascii", kw.get("ensure_ascii", "True")),
            ("separators", kw.get("separators", "default")),
            ("sort_keys", kw.get("sort_keys", "False")),
            ("indent", kw.get("indent", "None")),
        }
    )


def _peel_encode(node: ast.AST) -> ast.AST:
    """Peel ``x.encode(...)`` wrappers to the serialized value underneath."""
    cur = node
    while (
        isinstance(cur, ast.Call)
        and isinstance(cur.func, ast.Attribute)
        and cur.func.attr == "encode"
    ):
        cur = cur.func.value
    return cur


@dataclass(frozen=True)
class Measurement:
    path: str
    function: str
    call: str
    kwargs: tuple[tuple[str, str], ...]

    @property
    def form(self) -> frozenset[tuple[str, str]]:
        return _form(self.kwargs)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.function, self.call)

    def __str__(self) -> str:
        return f"{self.path}:{self.function}: {self.call}"


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return "<module>"


def _find_measurements(source: str, path: str) -> list[Measurement]:
    """Find every ``len(json.dumps(...))``-style size measurement in ``source``."""
    tree = ast.parse(source, filename=path)
    parents: dict[ast.AST, ast.AST] = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }

    # Track simple ``s = json.dumps(...)`` bindings for the two-step idiom.
    stored: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _is_serializer(node.value)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    stored.setdefault(target.id, []).append(node.value)

    found: list[Measurement] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "len"
            and node.args
        ):
            continue
        arg = node.args[0]
        candidates: list[ast.Call] = []

        # Direct form: len(json.dumps(x, ...)) or len(json.dumps(x, ...).encode(...))
        peeled = _peel_encode(arg)
        if isinstance(peeled, ast.Call) and _is_serializer(peeled):
            candidates.append(peeled)

        # Two-step form: s = json.dumps(...); len(s) / len(s.encode(...))
        inner = arg
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "encode"
        ):
            inner = inner.func.value
        if isinstance(inner, ast.Name):
            candidates.extend(stored.get(inner.id, []))

        for call in candidates:
            found.append(
                Measurement(
                    path=path,
                    function=_enclosing_function(node, parents),
                    call=ast.unparse(call),
                    kwargs=_semantic_kwargs(call),
                )
            )
    return found


@lru_cache(maxsize=None)
def _sweep(commit: str) -> tuple[Measurement, ...]:
    reader = _make_blob_reader(commit)
    found: list[Measurement] = []
    for path in _production_files(commit):
        found.extend(_find_measurements(reader(path), path))
    return tuple(found)


# ---------------------------------------------------------------------------
# Persist-form extraction (read from HEAD; never a hand-copied literal)
# ---------------------------------------------------------------------------


def _serializers_in_function(
    tree: ast.AST, name: str, parents: dict[ast.AST, ast.AST]
) -> list[ast.Call]:
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        ),
        None,
    )
    assert fn is not None, f"persist function {name!r} not found in HEAD blob"
    calls = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _is_serializer(node):
            # Ignore calls belonging to a nested function definition.
            if _enclosing_function(node, parents) == name:
                calls.append(node)
    return calls


def _extract_persist_form(
    read: Callable[[str], str], path: str, function: str, selector: str
) -> frozenset[tuple[str, str]]:
    """Extract the serialization form of the value a sink persists/sends."""
    tree = ast.parse(read(path), filename=path)
    parents: dict[ast.AST, ast.AST] = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    calls = _serializers_in_function(tree, function, parents)
    assert calls, f"{path}:{function}: no serializer call found (sink drifted)"
    chosen = calls[0] if selector == "first" else calls[-1]
    if selector == "only":
        assert len(calls) == 1, (
            f"{path}:{function}: expected exactly 1 serializer, found {len(calls)}"
        )
    return _form(_semantic_kwargs(chosen))


# PERSIST_FORMS maps a short id -> (HEAD path, function, selector) of the sink
# whose serialization form the corresponding measurement must match.
PERSIST_FORMS: dict[str, tuple[str, str, str]] = {
    "artifact_repo_json_dumps": (
        "src/core/artifacts/repository.py",
        "_json_dumps",
        "only",
    ),
    "interaction_create_plaintext": (
        "src/core/applications/interactions/handler.py",
        "create_interaction",
        "only",
    ),
    "post_signed_body": ("src/core/plexijoin/manager.py", "_post_signed", "only"),
    "webhook_canonical": (
        "src/core/webhooks/canonical.py",
        "canonical_payload_bytes",
        "only",
    ),
    "dsar_export": ("src/core/dsar/export_formats.py", "generate_json", "first"),
    "suite_stash_json": (
        "src/core/suite/stash/stash_manager_base.py",
        "_json",
        "only",
    ),
    "serialize_frame": ("src/api/websocket/connection.py", "_serialize_frame", "only"),
}


@dataclass(frozen=True)
class Site:
    kwargs: tuple[tuple[str, str], ...]
    persist: str  # key in PERSIST_FORMS | "self" | "transport"
    reason: str


# Every production serialization-size measurement in HEAD.  ``kwargs`` is
# pinned (any change fails drift detection); ``persist`` names the sink whose
# form is verified against HEAD, with a written justification for each.
SITE_REGISTRY: dict[tuple[str, str, str], Site] = {
    # -- interaction callback validation: this exact string is the response body
    (
        "src/api/routes/applications.py",
        "_validate_interaction_callback_data",
        "json.dumps(data)",
    ): Site(
        (),
        "self",
        "Callback response is transmitted as this exact default json.dumps(data) string.",
    ),
    (
        "src/api/routes/applications.py",
        "_validate_interaction_callback_data",
        "json.dumps(value)",
    ): Site(
        (),
        "self",
        "Complex callback fields are bounded with the default json.dumps form.",
    ),
    # -- FIXED sites: now measure the persisted/transmitted default form
    (
        "src/api/routes/artifacts/routes/crud.py",
        "update_artifact",
        "json.dumps(update_fields['payload'])",
    ): Site(
        (),
        "artifact_repo_json_dumps",
        "693f4c2a-class fix: artifact payload is persisted via _json_dumps (default form).",
    ),
    (
        "src/api/schemas/artifacts.py",
        "_validate_payload_size",
        "json.dumps(value)",
    ): Site(
        (),
        "artifact_repo_json_dumps",
        "693f4c2a-class fix: create-request payload persists via _json_dumps (default form).",
    ),
    (
        "src/api/routes/join.py",
        "send_join_message",
        "json.dumps(payload, default=str)",
    ): Site(
        (("default", "str"),),
        "post_signed_body",
        "Federated payload is sent by _post_signed's default json.dumps; default=str "
        "cannot shrink an already-serializable value.",
    ),
    (
        "src/core/applications/interactions/handler.py",
        "_validate_interaction_data",
        "json.dumps(data)",
    ): Site(
        (),
        "interaction_create_plaintext",
        "ae6b9936 fix: measures the exact default json.dumps(data) that create_interaction encrypts.",
    ),
    (
        "src/core/artifacts/repository.py",
        "append_artifact_op",
        "_json_dumps(data)",
    ): Site(
        (),
        "artifact_repo_json_dumps",
        "693f4c2a fix: the measured _json_dumps string IS the persisted op-log string.",
    ),
    # -- documented compact-canonical / pretty sinks (same form on both sides)
    (
        "src/core/push/manager/delivery.py",
        "_serialized_value_bytes",
        "json.dumps(value, sort_keys=True, separators=(',', ':'))",
    ): Site(
        (("separators", "compact"), ("sort_keys", "True")),
        "self",
        "Compact canonical push-data encoding; the serialized value is transmitted verbatim.",
    ),
    ("src/core/push/manager/apns.py", "_send_apns", "json.dumps(v)"): Site(
        (),
        "self",
        "APNs data value is measured as the exact default json.dumps string that is placed in the payload.",
    ),
    (
        "src/core/webhooks/delivery.py",
        "_payload_serialized_size",
        "json.dumps(payload, sort_keys=True, separators=(',', ':'))",
    ): Site(
        (("separators", "compact"), ("sort_keys", "True")),
        "webhook_canonical",
        "Compact canonical webhook bytes (sort_keys + compact separators) are what canonical_payload_bytes signs and sends.",
    ),
    (
        "src/core/dsar/privacy.py",
        "_export_serialized_size",
        "json.dumps(probe_envelope, indent=2, default=str)",
    ): Site(
        (("default", "str"), ("indent", "2")),
        "dsar_export",
        "DSAR export is pretty-printed with indent=2 on disk; the probe mirrors generate_json.",
    ),
    (
        "src/core/suite/boundaries.py",
        "bound_workspace_json",
        "json.dumps(value, separators=(',', ':'))",
    ): Site(
        (("separators", "compact"),),
        "suite_stash_json",
        "Suite workspace JSON is persisted with compact separators by the _json helper.",
    ),
    (
        "src/core/suite/stash/stash_manager_base.py",
        "_bound_metadata",
        "json.dumps(metadata, separators=(',', ':'))",
    ): Site(
        (("separators", "compact"),),
        "suite_stash_json",
        "Stash metadata is persisted by _json with the same compact separators.",
    ),
    # -- gateway transport heuristics (NOT storage caps; divergences documented)
    (
        "src/api/websocket/artifacts.py",
        "cap_snapshot_payload",
        "json.dumps(payload, ensure_ascii=False)",
    ): Site(
        (("ensure_ascii", "False"),),
        "transport",
        "Truncation heuristic, not a storage cap: an oversized snapshot is omitted and the client "
        "fetches over REST; the gateway frame byte cap bounds the transmitted frame independently.",
    ),
    (
        "src/api/websocket/boundaries.py",
        "_measure_payload_size",
        "json.dumps(value, ensure_ascii=False, default=str)",
    ): Site(
        (("default", "str"), ("ensure_ascii", "False")),
        "transport",
        "Inbound gateway frame guard: measures attacker JSON node cost; the actual frame byte cap "
        "is enforced separately at the transport boundary. Value is not persisted in this form.",
    ),
}


# ---------------------------------------------------------------------------
# Core comparison helper (shared by the real tree and the self-tests)
# ---------------------------------------------------------------------------


def _assert_representation_matches(
    measured: frozenset[tuple[str, str]],
    persisted: frozenset[tuple[str, str]],
    label: str,
) -> None:
    assert measured == persisted, (
        f"{label}: serialization-size measurement form does not match the "
        f"persisted/transmitted representation form\n"
        f"  measurement: {sorted(measured)}\n"
        f"  persisted:   {sorted(persisted)}"
    )


# ---------------------------------------------------------------------------
# Non-vacuous self-tests (the detectors actually flag mismatches)
# ---------------------------------------------------------------------------


def test_self_test_mismatch_is_flagged():
    """Synthetic compact measurement + default persist must be flagged."""
    measured = _form(
        (("ensure_ascii", "False"),)
    )  # len(json.dumps(x, ensure_ascii=False))
    persisted = _form(())  # json.dumps(x)
    with pytest.raises(AssertionError, match="does not match"):
        _assert_representation_matches(measured, persisted, "synthetic")


def test_self_test_matched_forms_are_accepted():
    """Matched forms (default and compact-canonical) must pass."""
    _assert_representation_matches(_form(()), _form(()), "synthetic-default")
    canonical = _form((("separators", "compact"), ("sort_keys", "True")))
    _assert_representation_matches(canonical, canonical, "synthetic-canonical")
    # default=str alone is NOT size-affecting and keeps the default form.
    _assert_representation_matches(
        _form((("default", "str"),)), _form(()), "synthetic-default-str"
    )


def test_self_test_detector_extracts_compact_kwargs():
    """The detector must really extract ensure_ascii=False (not vacuously {})."""
    source = (
        "import json\n"
        "def check(x):\n"
        "    size = len(json.dumps(x, ensure_ascii=False).encode('utf-8'))\n"
        "    return size\n"
    )
    found = _find_measurements(source, "src/_synthetic.py")
    assert len(found) == 1, found
    assert dict(found[0].kwargs).get("ensure_ascii") == "False", found[0]
    assert ("ensure_ascii", "False") in found[0].form


def test_self_test_detector_handles_two_step_and_typed_kwargs():
    """Both the ``s = json.dumps(...)`` idiom and canonical kwargs are seen."""
    source = (
        "import json\n"
        "def f(v):\n"
        "    encoded = json.dumps(v, sort_keys=True, separators=(',', ':'))\n"
        "    size = len(encoded)\n"
        "    other = json.dumps(v)\n"
        "    return len(other.encode('utf-8'))\n"
    )
    found = {
        (m.call, m.kwargs) for m in _find_measurements(source, "src/_synthetic.py")
    }
    assert (
        "json.dumps(v, sort_keys=True, separators=(',', ':'))",
        (("separators", "compact"), ("sort_keys", "True")),
    ) in found, found
    assert ("json.dumps(v)", ()) in found, found


def test_self_test_new_unregistered_site_is_flagged():
    """A measurement site absent from the registry must surface as new."""
    source = "import json\ndef f(x):\n    return len(json.dumps(x))\n"
    found = {m.key for m in _find_measurements(source, "src/_brand_new.py")}
    registry = {
        ("src/api/websocket/artifacts.py", "cap_snapshot_payload", "json.dumps(x)")
    }
    new = found - registry
    assert new, "detector failed to flag a brand-new unregistered measurement site"


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"

    measurements = _sweep(commit_sha)
    keys = {m.key for m in measurements}
    assert len(keys) == len(measurements), "duplicate measurement keys in sweep"
    assert set(SITE_REGISTRY) <= keys, (
        "registry references sites the committed-tree sweep did not find: "
        f"{sorted(set(SITE_REGISTRY) - keys)}"
    )
    # The detector distinguishes forms: both the default form and at least one
    # non-default (compact/indent) form must actually be present in HEAD.
    forms = {m.form for m in measurements}
    assert _form(()) in forms, "no default-form measurement found (vacuous scan)"
    assert any(f != _form(()) for f in forms), "no non-default-form measurement found"


def test_measurement_set_matches_registry(commit_sha):
    """No new/removed size check may exist without a reviewed registry entry."""
    keys = {m.key for m in _sweep(commit_sha)}
    new = keys - set(SITE_REGISTRY)
    removed = set(SITE_REGISTRY) - keys
    assert not new and not removed, (
        "serialization-size measurement set drifted from the documented registry.\n"
        f"  new: {sorted(new)}\n"
        f"  removed: {sorted(removed)}"
    )


def test_measurement_kwargs_have_not_drifted(commit_sha):
    """Any kwarg change on a known measurement (esp. a new compact form) fails."""
    for measurement in _sweep(commit_sha):
        spec = SITE_REGISTRY.get(measurement.key)
        assert spec is not None, f"unregistered measurement site: {measurement}"
        assert measurement.kwargs == spec.kwargs, (
            f"{measurement}: serialization kwargs drifted from the pinned form\n"
            f"  got:      {measurement.kwargs}\n"
            f"  expected: {spec.kwargs}"
        )


def test_persist_forms_match_measurement_forms(commit_sha):
    """For persist-bound sites, assert measure form == sink form from HEAD."""
    reader = _make_blob_reader(commit_sha)
    for measurement in _sweep(commit_sha):
        spec = SITE_REGISTRY[measurement.key]
        assert spec.reason.strip(), f"{measurement}: missing documentation reason"
        if spec.persist == "transport":
            # Allowlisted transport heuristic; reason documents the separate
            # enforcement. Kept explicit so it is reviewed, not silently ignored.
            continue
        if spec.persist == "self":
            persisted = measurement.form
        else:
            assert spec.persist in PERSIST_FORMS, (
                f"{measurement}: unknown persist ref {spec.persist!r}"
            )
            path, fn, selector = PERSIST_FORMS[spec.persist]
            persisted = _extract_persist_form(reader, path, fn, selector)
        _assert_representation_matches(measurement.form, persisted, str(measurement))


def test_fixed_sites_still_measure_default_form(commit_sha):
    """Regression net: the two fixed sites + the follow-up fixes stay default."""
    by_key = {m.key: m for m in _sweep(commit_sha)}
    fixed_keys = [
        (
            "src/core/applications/interactions/handler.py",
            "_validate_interaction_data",
            "json.dumps(data)",
        ),
        ("src/core/artifacts/repository.py", "append_artifact_op", "_json_dumps(data)"),
        (
            "src/api/routes/artifacts/routes/crud.py",
            "update_artifact",
            "json.dumps(update_fields['payload'])",
        ),
        ("src/api/schemas/artifacts.py", "_validate_payload_size", "json.dumps(value)"),
    ]
    for key in fixed_keys:
        assert key in by_key, f"fixed site disappeared from sweep: {key}"
        assert by_key[key].form == _form(()), (
            f"{key}: regressed to a non-default (compact) measurement form"
        )
