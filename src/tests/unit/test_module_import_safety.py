"""Repo-wide guard: what an import may and may not depend on.

``test_optional_dep_import_warning.py`` pins the four modules that were once
broken by an import-time ``logger.*`` call. This file closes the class of bug
for good. It imports *every* module in the production tree (``src/core``,
``src/api``, ``src/utils``, ``src/server``, ``src/cli``, ``src/bootstrap`` and
the top-level modules) in a fresh interpreter, twice, and fails on anything
that does not match the documented contract.

The two prerequisites
---------------------
``config.setup()`` is a real prerequisite: the CLI, the server bootstrap and
the test suite all run it before the API modules are imported, and modules
such as ``core/ratelimit/config.py`` read configuration while their
module-level rate limit tables are built. ``logger.setup()`` is *not*: it
exists to report diagnostics, so a missing logger must never be the reason an
import fails -- that is exactly what took down ``utils.encryption.vault``,
``core.voice.signaling.sfu.mediasoup_ws`` and every package importing them.

The two sweeps pin both halves of that contract:

``test_every_production_module_imports``
    config is set up, the logger is left unconfigured. Every module must
    import, and nothing may print while it does.

``test_missing_config_fails_only_the_documented_way``
    config is *not* set up. A module that needs it must fail with the
    documented, actionable ``RuntimeError`` from ``utils.config`` -- never
    with a confusing secondary crash (``TypeError``, ``KeyError``, ...) that
    swallows the real problem. Callers who have not yet called
    ``config.setup()`` therefore get one clear instruction rather than a
    traceback from deep inside a library.

Why a subprocess
----------------
The pytest process has already called ``logger.setup()`` and ``config.setup()``
(see ``src/tests/_conftest/config_env.py``) and cannot un-configure them for
the whole tree without breaking the rest of the session, so both sweeps run in
a child interpreter. The child is also given a throwaway working directory so
that any module creating relative paths at import time cannot litter the
checkout.

The sweeps are non-vacuous
--------------------------
Each child first plants a one-line module reproducing the bug in question,
appends it to the module list, and reports it alongside the real results. The
test asserts that probe *was* reported as a failure, so a green run proves the
detection path works rather than simply returning an empty list:

* the logger sweep plants a module calling ``logger.warning(...)`` at import;
* the config sweep plants a module that swallows the documented
  ``RuntimeError`` and then misuses the missing value, which must surface as a
  non-documented failure.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]

_REPORT_MARKER = "SWEEP_REPORT"

# The one error a config-dependent import is allowed to raise before setup().
CONFIG_REQUIRED_MESSAGE = (
    "Config not configured. Please call config.setup() in your main.py file first."
)

# Run as ``python -c`` in the child. Kept as a string (not a file) on purpose:
# the repo must not accumulate sweep scripts, and the child needs no imports
# from the test process.
_SWEEP = """
import importlib
import io
import json
import os
import sys
import tempfile


def _discover(root):
    found = []
    src = os.path.join(root, "src")
    for dirpath, dirnames, filenames in os.walk(src):
        # Test packages have their own import conventions and are not the
        # invariant under test here.
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "tests")]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root)
            if filename == "__init__.py":
                name = rel[: -len(os.sep + "__init__.py")].replace(os.sep, ".")
            else:
                name = rel[: -len(".py")].replace(os.sep, ".")
            found.append(name)
    return sorted(set(found))


def main():
    root = sys.argv[1]
    mode = sys.argv[2]
    for path in (
        root,
        os.path.join(root, "src"),
        os.path.join(root, "src", "utils"),
        os.path.join(root, "src", "utils", "common_utils"),
    ):
        if path not in sys.path:
            sys.path.insert(0, path)

    import utils.config as config
    import utils.logger as logger

    if mode == "configured":
        # A configured config is the documented prerequisite; the logger is
        # deliberately left unconfigured.
        config.setup(
            config_path=os.path.join(tempfile.mkdtemp(), "config.yaml"),
            default_config={},
        )
        if logger._setup_called:
            raise SystemExit("precondition failed: the logger is already set up")
        probe_source = (
            "import utils.logger as logger\\nlogger.warning('probe')\\n"
        )
    else:
        if config._setup_called:
            raise SystemExit("precondition failed: config is already set up")
        # The misuse this sweep exists to catch: swallow the documented error
        # and then trip over the missing value, so the caller sees a
        # confusing secondary crash instead of the setup instruction.
        probe_source = (
            "import utils.config as config\\n"
            "try:\\n"
            "    value = config.get('probe_key')\\n"
            "except RuntimeError:\\n"
            "    value = None\\n"
            "value['missing']\\n"
        )

    probe_dir = tempfile.mkdtemp()
    with open(os.path.join(probe_dir, "import_probe.py"), "w") as handle:
        handle.write(probe_source)
    sys.path.insert(0, probe_dir)

    modules = _discover(root)
    modules.append("import_probe")

    # Import-time printing is the tell-tale of a script masquerading as a
    # module, so the sweep collects it as a failure too.
    printed = io.StringIO()
    real_stdout = sys.stdout
    failures = []
    sys.stdout = printed
    try:
        for name in modules:
            try:
                importlib.import_module(name)
            except BaseException as exc:  # noqa: BLE001 - the type is the payload
                failures.append(
                    {
                        "module": name,
                        "error": type(exc).__name__,
                        "message": str(exc),
                    }
                )
    finally:
        sys.stdout = real_stdout

    report = {
        "mode": mode,
        "scanned": len(modules),
        "probe_detected": any(f["module"] == "import_probe" for f in failures),
        "failures": failures,
        "import_output": printed.getvalue()[:4000],
    }
    sys.stdout.write("SWEEP_REPORT" + json.dumps(report) + "\\n")


main()
"""


def _run_sweep(tmp_path, mode: str) -> dict:
    process = subprocess.run(
        [sys.executable, "-c", _SWEEP, str(PROJECT_ROOT), mode],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=600,
    )
    for line in process.stdout.splitlines():
        if line.startswith(_REPORT_MARKER):
            return json.loads(line[len(_REPORT_MARKER) :])
    raise AssertionError(
        f"the {mode} import sweep produced no report\n"
        f"exit code: {process.returncode}\n"
        f"stdout:\n{process.stdout}\n"
        f"stderr:\n{process.stderr}"
    )


@pytest.mark.unit
class TestModuleImportsBeforeLoggerSetup:
    def test_every_production_module_imports(self, tmp_path):
        report = _run_sweep(tmp_path, "configured")

        # Guard against a vacuous pass: if discovery silently returned nothing
        # the assertions below would hold trivially.
        assert report["scanned"] > 900, (
            f"only {report['scanned']} modules were discovered - "
            "the sweep is not looking where it should"
        )
        assert report["probe_detected"], (
            "harness self-check failed: a planted import-time logger call was "
            "not reported, so this test cannot detect the bug it guards"
        )

        failures = [f for f in report["failures"] if f["module"] != "import_probe"]
        assert not failures, (
            "these modules cannot be imported before logger.setup():\n"
            + "\n".join(
                f"  {f['module']}: {f['error']}: {f['message']}" for f in failures[:20]
            )
        )

        # A module that prints while being imported is a script pretending to
        # be a module: its output lands in whatever program imported it.
        assert report["import_output"] == "", (
            f"importing production modules wrote to stdout:\n{report['import_output']}"
        )


@pytest.mark.unit
class TestModuleImportsBeforeConfigSetup:
    def test_missing_config_fails_only_the_documented_way(self, tmp_path):
        report = _run_sweep(tmp_path, "unconfigured")

        assert report["scanned"] > 900, (
            f"only {report['scanned']} modules were discovered - "
            "the sweep is not looking where it should"
        )
        assert report["probe_detected"], (
            "harness self-check failed: a planted module that swallows the "
            "documented config error and then misuses the missing value was "
            "not reported, so this test cannot detect the bug it guards"
        )

        failures = [f for f in report["failures"] if f["module"] != "import_probe"]

        # Non-vacuous in the other direction too: the prerequisite only means
        # anything if some module actually enforces it. If this ever drops to
        # zero the sweep is no longer exercising the contract, so say so
        # rather than passing on an empty list.
        assert failures, (
            "no module failed on the missing config - either every import "
            "stopped needing it (then delete this sweep) or the sweep is "
            "no longer reaching the modules it should"
        )

        unexpected = [
            f
            for f in failures
            if f["error"] != "RuntimeError" or f["message"] != CONFIG_REQUIRED_MESSAGE
        ]
        assert not unexpected, (
            "these modules fail on a missing config with something other than "
            "the documented RuntimeError, so callers get a confusing crash "
            "instead of the config.setup() instruction:\n"
            + "\n".join(
                f"  {f['module']}: {f['error']}: {f['message']}"
                for f in unexpected[:20]
            )
        )

        assert report["import_output"] == "", (
            f"importing production modules wrote to stdout:\n{report['import_output']}"
        )
