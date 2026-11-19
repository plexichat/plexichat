"""SBOM / compiled-lock / declared-dependency drift regressions.

The shipped ``scripts/generate_sbom.py`` used to build the Python CycloneDX
SBOM from ``pip freeze`` of the build interpreter. In this checkout that
ambient tree contained 30 distributions absent from both compiled locks --
including packages the project deliberately removed (``psycopg2-binary``,
``redis``, ``fpdf2``, ``markdown``, ``odfpy``) -- 31 version mismatches against
``requirements.txt``, and 42 runtime distributions the SBOM never listed. Any
consumer of that SBOM got a false inventory of what actually ships.

The Python SBOM is now generated deterministically from the compiled,
hash-pinned ``requirements.txt`` and committed at ``sbom/python.cdx.json``.
These tests fail if that artifact, either compiled lock, or the declared
``.in`` pins drift apart, and pin the generator to the lockfile source.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "scripts" / "generate_sbom.py"
SBOM = REPO_ROOT / "sbom" / "python.cdx.json"
RUNTIME_LOCK = REPO_ROOT / "requirements.txt"
DEV_LOCK = REPO_ROOT / "requirements-dev.txt"
RUNTIME_IN = REPO_ROOT / "requirements.in"
DEV_IN = REPO_ROOT / "requirements-dev.in"

PIN_RE = re.compile(r"(?m)^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-fA-F]{64})")
DIRECT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;]+)")
VIA_REF_RE = re.compile(r"(?m)^\s*#\s{2,}([A-Za-z0-9][A-Za-z0-9._-]*)\s*$")

# Removed on purpose; see the rationale comments in requirements.in. Their
# presence in a freeze-derived SBOM is the concrete regression this guards.
BANNED = ("psycopg2-binary", "redis", "fpdf2", "markdown", "odfpy", "apt-listchanges")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _load_generator():
    spec = importlib.util.spec_from_file_location("plexichat_generate_sbom", GENERATOR)
    assert spec and spec.loader, f"cannot load {GENERATOR}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_lock(path: Path) -> dict[str, tuple[str, set[str]]]:
    """Parse a compiled lock into ``{name: (version, sha256 hashes)}``."""
    out: dict[str, tuple[str, set[str]]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = PIN_RE.match(line)
        if m:
            name = _norm(m.group(1))
            assert name not in out, f"duplicate distribution {name} in {path.name}"
            current = name
            out[name] = (m.group(2), set())
        if current is not None:
            for digest in HASH_RE.findall(line):
                out[current][1].add(digest.lower())
    return out


def _load_sbom() -> dict:
    assert SBOM.is_file(), f"missing committed SBOM at {SBOM}"
    return json.loads(SBOM.read_text(encoding="utf-8"))


def test_committed_sbom_matches_runtime_lock() -> None:
    """Every committed SBOM component is exactly a pinned runtime dist."""
    bom = _load_sbom()
    assert bom.get("bomFormat") == "CycloneDX"

    props = {
        p["name"]: p["value"] for p in bom.get("metadata", {}).get("properties", [])
    }
    assert props.get("plexichat:sbom:source") == "lockfile"
    assert props.get("plexichat:sbom:lockfile") == RUNTIME_LOCK.name
    assert (
        props.get("plexichat:sbom:lockfile-sha256")
        == hashlib.sha256(RUNTIME_LOCK.read_bytes()).hexdigest()
    ), "committed SBOM was generated from a different requirements.txt"

    components = {c["name"]: c for c in bom["components"]}
    lock = _parse_lock(RUNTIME_LOCK)
    assert set(components) == set(lock), (
        "SBOM components and requirements.txt distributions diverged: "
        f"only-in-sbom={sorted(set(components) - set(lock))}, "
        f"only-in-lock={sorted(set(lock) - set(components))}"
    )
    for name, (version, hashes) in lock.items():
        component = components[name]
        assert component["version"] == version, (
            f"{name}: SBOM {component['version']} != lock {version}"
        )
        assert component["purl"] == f"pkg:pypi/{name}@{version}"
        sbom_hashes = {
            h["content"] for h in component.get("hashes", []) if h["alg"] == "SHA-256"
        }
        assert sbom_hashes == hashes, f"{name}: SBOM hashes != lock hashes"


def test_no_undeclared_or_removed_packages_in_sbom() -> None:
    """The ambient-environment packages that polluted the old SBOM stay out."""
    names = {c["name"] for c in _load_sbom()["components"]}
    lock = _parse_lock(RUNTIME_LOCK)
    assert not (names - set(lock)), "SBOM lists distributions absent from the lock"
    for banned in BANNED:
        assert banned not in names, (
            f"{banned} was deliberately removed from the runtime tree but is "
            "present in the SBOM -- the generator regressed to pip freeze"
        )


def test_direct_pins_resolve_in_compiled_locks() -> None:
    """Every ``==`` pin declared in an ``.in`` file appears in its compiled lock."""
    for declared, compiled in ((RUNTIME_IN, RUNTIME_LOCK), (DEV_IN, DEV_LOCK)):
        lock = _parse_lock(compiled)
        for line in declared.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            m = DIRECT_RE.match(line)
            if not m:
                continue
            name, version = _norm(m.group(1)), m.group(2)
            assert name in lock, f"{declared.name}: {name} missing from {compiled.name}"
            assert lock[name][0] == version, (
                f"{declared.name}: {name}=={version} but {compiled.name} "
                f"pins {lock[name][0]}"
            )


def test_compiled_locks_are_fully_hashed_with_resolving_via_refs() -> None:
    """No duplicate/unhashed dists and no ``# via`` ref to a non-existent dist."""
    for path in (RUNTIME_LOCK, DEV_LOCK):
        lock = _parse_lock(path)
        assert lock, f"no distributions parsed from {path.name}"
        for name, (_, hashes) in lock.items():
            assert hashes, f"{path.name}: {name} has no SHA-256 hash"

        refs = {
            _norm(m.group(1))
            for m in VIA_REF_RE.finditer(path.read_text(encoding="utf-8"))
        }
        unresolved = sorted(r for r in refs if r not in lock and not r.startswith("-r"))
        assert not unresolved, f"{path.name}: unresolved # via refs: {unresolved}"


def test_generator_reproduces_committed_sbom_from_the_lockfile(tmp_path) -> None:
    """The default source must stay the lockfile, byte-for-byte."""
    module = _load_generator()
    out = tmp_path / "sbom"
    assert module.main(["--sbom-only", "--out", str(out)]) == 0
    generated = (out / "python.cdx.json").read_bytes()
    assert generated == SBOM.read_bytes(), (
        "generate_sbom.py no longer reproduces sbom/python.cdx.json from "
        "requirements.txt; regenerate the artifact and re-check the diff"
    )


def test_environment_source_remains_an_explicit_opt_in(tmp_path) -> None:
    """The legacy pip-freeze snapshot is preserved but never the default."""
    module = _load_generator()
    out = tmp_path / "env"
    assert (
        module.main(["--sbom-only", "--source", "environment", "--out", str(out)]) == 0
    )
    assert (out / "python.freeze.txt").is_file()
    env_bom = json.loads((out / "python.cdx.json").read_text(encoding="utf-8"))
    props = {
        p["name"]: p["value"] for p in env_bom.get("metadata", {}).get("properties", [])
    }
    assert props.get("plexichat:sbom:source") != "lockfile"
