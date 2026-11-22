"""Tests for ``ci-tooling/audit_failopen.py``.

The audit tool is the executable replacement for the hand-written findings
annexes. Its value depends on it actually flagging the bug shapes it claims to
flag, so these tests feed it synthetic modules and assert on the hits, then
assert the real repository is clean.

Two properties are covered:

1. Detection -- an unauthenticated route, a mutating route that ignores the
   caller identity, a fail-open manager guard, and a fail-open client helper
   are each reported.
2. Non-detection -- a route that authenticates and authorizes, and an
   auth-establishing route that verifies a credential, are not reported.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "audit_failopen.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("audit_failopen", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_failopen"] = module
    spec.loader.exec_module(module)
    return module


af = _load_tool()


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def synthetic(tmp_path: Path) -> Path:
    """A miniature server tree containing one of each bug shape."""
    _write(
        tmp_path,
        "src/core/example/manager.py",
        """
from typing import TYPE_CHECKING


class ExampleManager:
    def __init__(self):
        self._servers = None

    if TYPE_CHECKING:
        def update_thing(self, user_id, thing_id) -> None: ...

    def can_act(self):
        # Fail open: the guard grants instead of denying.
        if not self._servers:
            return True
        return self._servers.check()

    def can_act_safely(self):
        # Fail closed: the reference shape.
        if not self._servers:
            return False
        return self._servers.check()

    def delete_thing(self, user_id, thing_id):
        # Manager write that never uses the caller identity.
        self._db.execute("DELETE FROM things WHERE id = ?", (thing_id,))

    def delete_own_thing(self, user_id, thing_id):
        row = self._db.fetch_one("SELECT * FROM things WHERE id = ?", (thing_id,))
        if row and row["owner_id"] != user_id:
            raise PermissionDeniedError("not yours")
        self._db.execute("DELETE FROM things WHERE id = ?", (thing_id,))

    def delete_thing_via_delegate(self, user_id, thing_id):
        self._things.remove(user_id, thing_id)
""",
    )
    _write(
        tmp_path,
        "src/api/routes/example.py",
        """
from fastapi import APIRouter, Depends, Request

router = APIRouter()


@router.post("/no-auth")
async def no_auth(body: dict) -> dict:
    return {"ok": True}


@router.post("/ignores-caller")
async def ignores_caller(
    user=Depends(get_current_user), body: dict = None
) -> dict:
    return do_the_thing(body)


@router.post("/authorized")
async def authorized(request: Request, body: dict = None) -> dict:
    check_host_restriction(request)
    require_admin_permission(request, "example.manage")
    return {"ok": True}


@router.post("/self-scoped")
async def self_scoped(current_user=Depends(get_current_user)) -> dict:
    return {"id": current_user.user_id}


@router.delete("/@me")
async def delete_me(current_user=Depends(get_current_user)) -> dict:
    return {"ok": True}


@router.post("/verify")
async def verify_credential(body: dict) -> dict:
    return {"ok": auth.verify_email(body["token"])}


# ``add_api_route`` with a non-literal ``methods`` expression. The decorator
# sweeps cannot see these, and resolving a variable/loop verb to "no methods"
# used to drop them from this sweep too, so an unauthenticated mutation passed
# every guard. The guarded sibling and the literal read-only registration are
# controls: neither may be reported.
MUTATING_METHODS = ["POST", "DELETE"]


async def dynamic_nuke(request: Request, body: dict = None) -> dict:
    return do_the_thing(body)


async def dynamic_ok(
    request: Request, current_user=Depends(get_current_user), body: dict = None
) -> dict:
    return do_the_thing(body)


router.add_api_route("/dynamic-nuke", dynamic_nuke, methods=MUTATING_METHODS)
router.add_api_route("/dynamic-ok", dynamic_ok, methods=MUTATING_METHODS)
router.add_api_route("/dynamic-readonly", dynamic_nuke, methods=["GET"])
for _verb in ("POST",):
    router.add_api_route("/dynamic-loop", dynamic_nuke, methods=[_verb])
""",
    )
    _write(
        tmp_path,
        "src/api/websocket/handlers/example.py",
        """
class Handlers:
    async def handle_unauthenticated(self, connection, data):
        return None, None, None

    async def handle_authenticated(self, connection, data):
        if not connection.is_authenticated:
            return None, None, 4004
        return None, None, None
""",
    )
    _write(
        tmp_path,
        "src/core/probe/guards.py",
        """
from typing import TYPE_CHECKING, Any, Optional


class ProbeGuards:
    # The module-or-manager facade shape (sweep F).

    if TYPE_CHECKING:
        def _voice_manager(self) -> Optional[Any]: ...

    def _require_thing_permission(self, user_id, channel_id):
        # Fail open: the helpers are probed off a facade that may be the
        # module, and an unresolvable helper bails out of the guard.
        manager = self._voice_manager()
        if manager is None:
            return
        get_channel = getattr(manager, "_get_server_channel", None)
        require_permission = getattr(manager, "_require_permission", None)
        if get_channel is None or require_permission is None:
            return
        channel = get_channel(channel_id)
        require_permission(user_id, channel, "thing.connect", channel_id)

    def _require_other_permission(self, user_id, channel_id):
        # Fail closed: the same probe, but the missing branch denies.
        manager = self._voice_manager()
        get_channel = getattr(manager, "_get_server_channel", None)
        require_permission = getattr(manager, "_require_permission", None)
        if get_channel is None or require_permission is None:
            raise PermissionDeniedError("cannot verify")
        require_permission(user_id, channel_id, "thing.speak", channel_id)

    def schedule_thing(self, thing_id):
        # Feature detection on non-authorization plumbing: not a guard.
        db = getattr(self, "_db", None)
        if db is None:
            return
        schedule(thing_id, db)
""",
    )
    _write(
        tmp_path,
        "src/core/voice/example_sdp.py",
        """
import secrets


def build_fabricated_answer(transport):
    ice_ufrag = transport.get("usernameFragment", secrets.token_hex(4))
    ice_pwd = transport.get("password", secrets.token_hex(12))
    lines = [
        "v=0",
        f"a=ice-ufrag:{ice_ufrag}",
        f"a=ice-pwd:{ice_pwd}",
    ]
    return "\\r\\n".join(lines)


def build_answer_from_transport(transport):
    ice_ufrag = transport.ice_parameters["usernameFragment"]
    ice_pwd = transport.ice_parameters["password"]
    lines = [
        "v=0",
        f"a=ice-ufrag:{ice_ufrag}",
        f"a=ice-pwd:{ice_pwd}",
    ]
    return "\\r\\n".join(lines)


def generate_session_id():
    # Randomness with no SDP attribute nearby: not a fabricated credential.
    return secrets.token_hex(16)
""",
    )
    _write(
        tmp_path,
        "src/js/example.js",
        """
function canEdit(artifact) {
    if (!window.AppState.currentUser) return true;
    return artifact.author_id === window.AppState.currentUser.id;
}
""",
    )
    return tmp_path


def test_flags_fail_open_manager_guard(synthetic: Path) -> None:
    kinds = {hit.symbol: hit.kind for hit in af.sweep_core(synthetic)}
    assert kinds["_servers"] == af.GRANT
    # The sibling helper that denies must not be reported.
    grants = [h for h in af.sweep_core(synthetic) if h.kind == af.GRANT]
    assert len(grants) == 1


def test_flags_route_without_authentication(synthetic: Path) -> None:
    hits = af.sweep_routes(synthetic)
    no_auth = {h.symbol for h in hits if h.kind == af.NO_AUTH}
    assert "POST /no-auth" in no_auth


def test_flags_mutating_route_that_ignores_caller(synthetic: Path) -> None:
    hits = af.sweep_routes(synthetic)
    unauthorized = {h.symbol for h in hits if h.kind == af.UNAUTHZ}
    assert "POST /ignores-caller" in unauthorized


def test_flags_dynamic_route_with_unresolvable_methods(synthetic: Path) -> None:
    """Non-vacuity: a non-literal ``methods`` expression fails closed.

    A module-level list, a loop variable, a star-unpack or a ``**kwargs`` bag
    can hide a mutating verb. Such a registration must be checked, not dropped
    as "no methods"; an unauthenticated one is a NO_AUTH hit. The guarded
    sibling and the literal read-only registration are controls.
    """
    hits = af.sweep_routes(synthetic)
    flagged = {h.symbol for h in hits if h.kind in (af.NO_AUTH, af.UNAUTHZ)}
    assert any("/dynamic-nuke" in s for s in flagged), (
        "an add_api_route with methods=<variable> was silently skipped"
    )
    assert any("/dynamic-loop" in s for s in flagged), (
        "an add_api_route with methods=[loop_var] was silently skipped"
    )
    assert not any("/dynamic-ok" in s for s in flagged), (
        "Depends(get_current_user) must still protect an unresolvable-method route"
    )
    assert not any("/dynamic-readonly" in s for s in flagged), (
        "a literal methods=['GET'] registration must not be treated as unresolved"
    )


def test_dynamic_methods_helper_flags_only_unresolvable_verbs() -> None:
    """Focused detector test: literal verbs resolve, dynamic ones fail closed."""
    source = """
async def handler(request):
    return None

router.add_api_route("/literal", handler, methods=["POST"])
router.add_api_route("/variable", handler, methods=METHODS)
router.add_api_route("/loop-var", handler, methods=[verb])
router.add_api_route("/starred", handler, methods=[*METHODS])
router.add_api_route("/kwargs", handler, **opts)
router.add_api_route("/single", handler, methods="POST")
router.add_api_route("/get", handler, methods=["GET"])
"""
    tree = ast.parse(source)
    handlers = {"handler": [("src/api/routes/example.py", "body")]}
    found = {
        (method, path)
        for method, path, _, _, _ in af._iter_unresolved_dynamic_routes(
            tree, source, "src/api/routes/example.py", handlers
        )
    }
    assert ("<unresolved>", "/variable") in found
    assert ("<unresolved>", "/loop-var") in found
    assert ("<unresolved>", "/starred") in found
    assert ("<unresolved>", "/kwargs") in found
    assert not any(path in {"/literal", "/single", "/get"} for _, path in found)


def test_does_not_flag_authorized_or_self_scoped_routes(synthetic: Path) -> None:
    flagged = {
        h.symbol
        for h in af.sweep_routes(synthetic)
        if h.kind in (af.NO_AUTH, af.UNAUTHZ)
    }
    assert "POST /authorized" not in flagged
    assert "POST /self-scoped" not in flagged
    assert "DELETE /@me" not in flagged
    # A route that verifies a credential is itself the auth step.
    assert "POST /verify" not in flagged


def test_flags_ws_handler_without_authentication(synthetic: Path) -> None:
    hits = af.sweep_ws_handlers(synthetic)
    no_auth = {h.symbol for h in hits if h.kind == af.NO_AUTH}
    assert "handle_unauthenticated" in no_auth
    assert "handle_authenticated" not in no_auth


def test_flags_manager_write_ignoring_its_caller(synthetic: Path) -> None:
    hits = af.sweep_managers(synthetic)
    unchecked = {h.symbol for h in hits if h.kind == af.UNCHECKED}
    assert "delete_thing" in unchecked
    # Ownership check, denial, delegation, and a TYPE_CHECKING stub are clear.
    assert "delete_own_thing" not in unchecked
    assert "delete_thing_via_delegate" not in unchecked
    assert "update_thing" not in unchecked


def test_real_repository_manager_writes_all_resolve_the_caller() -> None:
    hits = af.sweep_managers(REPO_ROOT)
    assert [h.path for h in hits if h.kind == af.UNCHECKED] == []


def test_flags_authz_guard_that_skips_on_unresolvable_helper(synthetic: Path) -> None:
    grants = [h for h in af.sweep_facades(synthetic) if h.kind == af.GRANT]
    # Both probed helpers are reported, and only for the guard that skips.
    assert {h.symbol for h in grants} == {"_get_server_channel", "_require_permission"}
    assert all("_require_thing_permission" in h.detail for h in grants)


def test_does_not_flag_authz_guard_that_denies(synthetic: Path) -> None:
    grants = [h for h in af.sweep_facades(synthetic) if h.kind == af.GRANT]
    assert not any("_require_other_permission" in h.detail for h in grants)


def test_does_not_flag_non_authorization_facade_probe(synthetic: Path) -> None:
    hits = af.sweep_facades(synthetic)
    assert ("src/core/probe/guards.py", "_db") not in {
        (h.path, h.symbol) for h in hits if h.kind == af.GRANT
    }
    assert "_db" in {h.symbol for h in hits if h.kind == af.NON_AUTH}


def test_real_repository_has_no_facade_probe_grants() -> None:
    assert [h for h in af.sweep_facades(REPO_ROOT) if h.kind == af.GRANT] == []


def test_flags_fail_open_client_helper(synthetic: Path) -> None:
    hits = af.sweep_client(synthetic)
    grants = {h.symbol for h in hits if h.kind == af.GRANT}
    assert "canEdit" in grants


def test_flags_sdp_answer_with_fabricated_ice_credentials(synthetic: Path) -> None:
    hits = af.sweep_sdp_fabrication(synthetic)
    flags = {h.kind for h in hits}
    assert flags == {af.FABRICATED}, hits
    assert all("build_fabricated_answer" in h.detail for h in hits)


def test_does_not_flag_transport_derived_or_unrelated_randomness(
    synthetic: Path,
) -> None:
    hits = af.sweep_sdp_fabrication(synthetic)
    # An answer built from real transport parameters is the reference shape.
    assert not any("build_answer_from_transport" in h.detail for h in hits)
    # A random session id outside an SDP builder is not a fabricated credential.
    assert not any("generate_session_id" in h.detail for h in hits)


def test_real_repository_has_no_sdp_credential_fabrication() -> None:
    assert af.sweep_sdp_fabrication(REPO_ROOT) == []


def test_real_repository_has_no_fail_open_grants() -> None:
    assert [h for h in af.sweep_core(REPO_ROOT) if h.kind == af.GRANT] == []


def test_real_repository_routes_all_authenticate_and_authorize() -> None:
    hits = af.sweep_routes(REPO_ROOT)
    assert [h.path for h in hits if h.kind == af.NO_AUTH] == []
    assert [h.path for h in hits if h.kind == af.UNAUTHZ] == []


def test_real_repository_ws_handlers_all_authenticate() -> None:
    hits = af.sweep_ws_handlers(REPO_ROOT)
    assert [h.symbol for h in hits if h.kind == af.NO_AUTH] == []


def test_real_repository_client_helpers_do_not_fail_open() -> None:
    client_root = REPO_ROOT.parent / "plexichat-client"
    if not client_root.is_dir():
        pytest.skip("plexichat-client checkout not present")
    assert [h for h in af.sweep_client(client_root) if h.kind == af.GRANT] == []


# ---------------------------------------------------------------------------
# Deterministic read/parse counts (no timing): the sweeps overlap heavily, and
# a regression that reintroduces a whole-tree walk must fail these.
# ---------------------------------------------------------------------------


def _count_reads_and_parses(monkeypatch: pytest.MonkeyPatch):
    """Patch ``Path.read_text``/``ast.parse`` to count actual calls.

    Returns ``(read_counts, get_parse_count)``; all counts are call counts, so
    the assertions below are deterministic and never depend on wall-clock time.
    """
    read_counts: Counter[str] = Counter()
    parse_count = 0
    real_read_text = Path.read_text
    real_parse = ast.parse

    def counting_read_text(self, *args, **kwargs):
        read_counts[str(self)] += 1
        return real_read_text(self, *args, **kwargs)

    def counting_parse(*args, **kwargs):
        nonlocal parse_count
        parse_count += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(ast, "parse", counting_parse)

    def get_parse_count() -> int:
        return parse_count

    return read_counts, get_parse_count


def test_sweep_routes_reads_and_parses_each_route_module_once(
    synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweep D used to read and parse every route module twice in one call.

    ``_build_handler_index`` walked and parsed the route tree, then the main
    loop walked, read and parsed it all a second time. Sharing one cache makes
    both passes use the same parsed tree.
    """
    read_counts, get_parses = _count_reads_and_parses(monkeypatch)

    af.sweep_routes(synthetic)

    route_reads = {
        path: n for path, n in read_counts.items() if "/src/api/routes/" in path
    }
    assert route_reads, "expected a route module in the synthetic tree"
    assert set(route_reads.values()) == {1}, (
        f"each route module must be read once per sweep; got {route_reads}"
    )
    route_modules = list((synthetic / "src" / "api" / "routes").rglob("*.py"))
    assert get_parses() == len(route_modules), (
        f"each route module must be parsed once; got {get_parses()} "
        f"for {len(route_modules)} modules"
    )


def test_report_reuses_one_read_and_parse_per_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full ``report()`` run reads/parses each module at most once.

    Every sweep used to walk the tree and read the same files again: a route
    module was read six times and a core module up to five. ``report`` now
    threads a single cache through every sweep, so each file is read once and
    parsed at most once regardless of how many sweeps inspect it.
    """
    root = tmp_path / "server"
    client = tmp_path / "client"
    _write(
        root, "src/core/sample/manager.py", "class M:\n    def f(self):\n        pass\n"
    )
    _write(
        root,
        "src/api/routes/sample.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n",
    )
    _write(
        root,
        "src/api/websocket/handlers/voice.py",
        "async def handle_voice_speaking(connection, data):\n    return None\n",
    )
    _write(
        client,
        "src/js/voice.js",
        "function send() { this._sendVoiceMessage(this.OPCODES.VOICE_SPEAKING, "
        "{ speaking: true }); }\n",
    )

    py_files = list((root / "src").rglob("*.py"))
    js_files = list((client / "src").rglob("*.js"))
    assert len(py_files) == 3 and len(js_files) == 1

    read_counts, get_parses = _count_reads_and_parses(monkeypatch)

    cache = af.SourceCache()
    text, fail_open, missing = af.report(root, client, root / "findings.md", cache)

    # The synthetic tree may legitimately have findings; this test is about
    # I/O counts, not about the audited verdict.
    assert isinstance(text, str)
    assert isinstance(fail_open, int) and isinstance(missing, int)

    # Ground truth: actual disk reads and parses match the cache's own counters.
    assert sum(read_counts.values()) == cache.reads
    assert get_parses() == cache.parses

    # Each module is read exactly once and parsed at most once, no matter how
    # many of the overlapping sweeps inspected it.
    assert cache.reads == len(py_files) + len(js_files), read_counts
    assert cache.parses <= len(py_files), get_parses()
    assert set(read_counts.values()) == {1}, read_counts

    # Each distinct glob pattern is performed once (the route/core/api walks
    # used to be repeated by several sweeps).
    assert cache.walks == 7, cache.walks
