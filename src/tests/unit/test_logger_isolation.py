"""Regression tests for the shared AppLogger isolation helpers.

These pin ``src.tests._conftest.logger_isolation``:

* ``isolated_logger`` must restore the process-global ``"AppLogger"`` level
  when its scope exits, and must advertise the level it set to the guard.
* the autouse ``_app_logger_level_guard`` must fail fast when a test leaves
  that level changed (at setup or teardown), while allowing the level set by
  an active ``isolated_logger`` scope.
* the autouse ``_global_singleton_guard`` must fail fast when a test leaves a
  guarded process-global singleton (``utils.logger`` / ``utils.config`` /
  ``utils.version``) replaced instead of restored.

Because the guard is what protects the whole suite, these tests call its
fixture body directly (through ``__wrapped__``) with a stub request instead of
depending on a real leak elsewhere.
"""

import logging
from types import SimpleNamespace

import pytest

from src.tests._conftest import logger_isolation as li

_APP_LOGGER = logging.getLogger("AppLogger")


@pytest.fixture
def restore_app_logger():
    """Guarantee this module's mutations never trip the real autouse guard."""
    previous_level = _APP_LOGGER.level
    previous_propagate = _APP_LOGGER.propagate
    yield
    _APP_LOGGER.setLevel(previous_level)
    _APP_LOGGER.propagate = previous_propagate


@pytest.fixture
def restore_singletons():
    """Guarantee this module's mutations never trip the real autouse guards."""
    before = li._snapshot_singletons()
    handlers = list(_APP_LOGGER.handlers)
    filters = list(_APP_LOGGER.filters)
    level = _APP_LOGGER.level
    propagate = _APP_LOGGER.propagate
    yield
    for handler in _APP_LOGGER.handlers[:]:
        _APP_LOGGER.removeHandler(handler)
    for filt in _APP_LOGGER.filters[:]:
        _APP_LOGGER.removeFilter(filt)
    for handler in handlers:
        _APP_LOGGER.addHandler(handler)
    for filt in filters:
        _APP_LOGGER.addFilter(filt)
    _APP_LOGGER.setLevel(level)
    _APP_LOGGER.propagate = propagate
    for (module, attribute), value in before.items():
        setattr(module, attribute, value)


def _guard(nodeid):
    """Return the level guard's generator bound to a stub ``request``."""
    request = SimpleNamespace(node=SimpleNamespace(nodeid=nodeid))
    return li._app_logger_level_guard.__wrapped__(request)


def _singleton_guard(nodeid):
    """Return the singleton guard's generator bound to a stub ``request``."""
    request = SimpleNamespace(node=SimpleNamespace(nodeid=nodeid))
    return li._global_singleton_guard.__wrapped__(request)


@pytest.mark.unit
class TestIsolatedLogger:
    def test_restores_previous_level(self, tmp_path, restore_app_logger):
        before = _APP_LOGGER.level
        with li.isolated_logger(log_dir=str(tmp_path / "logs"), level="ERROR"):
            assert _APP_LOGGER.level == logging.ERROR
        assert _APP_LOGGER.level == before

    def test_registers_and_pops_active_scope(self, tmp_path, restore_app_logger):
        baseline = li.expected_level()
        with li.isolated_logger(log_dir=str(tmp_path / "logs"), level="DEBUG"):
            assert li.expected_level() == logging.DEBUG
        assert li.expected_level() == baseline

    def test_suppresses_setup_errors(self, restore_app_logger, monkeypatch):
        before = _APP_LOGGER.level

        def _boom(**_kwargs):
            raise OSError("log dir unavailable")

        monkeypatch.setattr(li.logger, "setup", _boom)
        with li.isolated_logger(
            log_dir="unused", level="ERROR", suppress_setup_errors=True
        ):
            pass
        assert _APP_LOGGER.level == before
        assert li.expected_level() == li._BASELINE_LEVEL

    def test_setup_error_propagates_by_default(self, restore_app_logger, monkeypatch):
        def _boom(**_kwargs):
            raise OSError("log dir unavailable")

        monkeypatch.setattr(li.logger, "setup", _boom)
        with pytest.raises(OSError):
            with li.isolated_logger(log_dir="unused", level="ERROR"):
                pass  # pragma: no cover - setup fails before the body
        assert li.expected_level() == li._BASELINE_LEVEL

    def test_restores_handlers_and_instance(
        self, tmp_path, restore_app_logger, restore_singletons
    ):
        """Scoped setup() must not orphan the outer handlers/instance."""
        outer_dir = tmp_path / "outer"
        inner_dir = tmp_path / "inner"
        outer_dir.mkdir()
        li.logger.setup(log_dir=str(outer_dir), level="INFO")
        before_handlers = list(_APP_LOGGER.handlers)
        assert before_handlers, "expected outer setup to attach handlers"
        before_instance = li.logger._logger_instance
        with li.isolated_logger(log_dir=str(inner_dir), level="ERROR"):
            assert _APP_LOGGER.level == logging.ERROR
            assert li.logger._logger_instance is not before_instance
        assert [id(h) for h in _APP_LOGGER.handlers] == [id(h) for h in before_handlers]
        assert li.logger._logger_instance is before_instance

    def test_nested_scopes_restore_in_order(
        self, tmp_path, restore_app_logger, restore_singletons
    ):
        outer_dir = tmp_path / "outer"
        outer_dir.mkdir()
        li.logger.setup(log_dir=str(outer_dir), level="INFO")
        before_handlers = list(_APP_LOGGER.handlers)
        with li.isolated_logger(log_dir=str(tmp_path / "mid"), level="WARNING"):
            mid_handlers = list(_APP_LOGGER.handlers)
            assert _APP_LOGGER.level == logging.WARNING
            with li.isolated_logger(log_dir=str(tmp_path / "inner"), level="ERROR"):
                assert _APP_LOGGER.level == logging.ERROR
            assert _APP_LOGGER.level == logging.WARNING
            assert [id(h) for h in _APP_LOGGER.handlers] == [
                id(h) for h in mid_handlers
            ]
        assert [id(h) for h in _APP_LOGGER.handlers] == [id(h) for h in before_handlers]


@pytest.mark.unit
class TestLevelGuard:
    def test_passes_when_level_untouched(self, restore_app_logger):
        gen = _guard("demo.py::test_clean")
        next(gen)  # setup
        with pytest.raises(StopIteration):
            next(gen)  # teardown, clean completion

    def test_detects_leak_at_teardown(self, restore_app_logger):
        gen = _guard("demo.py::test_leaked")
        next(gen)  # setup
        _APP_LOGGER.setLevel(logging.DEBUG)
        with pytest.raises(pytest.fail.Exception):
            next(gen)

    def test_detects_pre_existing_leak_at_setup(self, restore_app_logger):
        _APP_LOGGER.setLevel(logging.ERROR)
        gen = _guard("demo.py::test_pre_leaked")
        with pytest.raises(pytest.fail.Exception):
            next(gen)

    def test_honours_active_scope(self, tmp_path, restore_app_logger):
        with li.isolated_logger(log_dir=str(tmp_path / "logs"), level="DEBUG"):
            gen = _guard("demo.py::test_scoped")
            next(gen)  # expects DEBUG (active scope)
            assert _APP_LOGGER.level == logging.DEBUG
            with pytest.raises(StopIteration):
                next(gen)  # teardown still allows DEBUG


@pytest.mark.unit
class TestGlobalSingletonGuard:
    def test_passes_when_untouched(self):
        gen = _singleton_guard("demo.py::test_clean")
        next(gen)  # setup
        with pytest.raises(StopIteration):
            next(gen)  # teardown, nothing changed

    def test_passes_when_restored_before_teardown(self, restore_singletons):
        gen = _singleton_guard("demo.py::test_restored")
        next(gen)
        original = li.config._config_instance
        li.config._config_instance = object()
        li.config._config_instance = original
        with pytest.raises(StopIteration):
            next(gen)

    def test_detects_replaced_config_instance(self, restore_singletons):
        gen = _singleton_guard("demo.py::test_config")
        next(gen)
        li.config._config_instance = object()
        with pytest.raises(pytest.fail.Exception) as excinfo:
            next(gen)
        assert "utils.config._config_instance" in str(excinfo.value)

    def test_detects_replaced_logger_instance(self, restore_singletons):
        gen = _singleton_guard("demo.py::test_logger")
        next(gen)
        li.logger._logger_instance = object()
        with pytest.raises(pytest.fail.Exception) as excinfo:
            next(gen)
        assert "utils.logger._logger_instance" in str(excinfo.value)

    def test_detects_changed_setup_flag(self, restore_singletons):
        gen = _singleton_guard("demo.py::test_flag")
        next(gen)
        li.config._setup_called = not li.config._setup_called
        with pytest.raises(pytest.fail.Exception) as excinfo:
            next(gen)
        assert "utils.config._setup_called" in str(excinfo.value)

    def test_detects_changed_version(self, restore_singletons):
        gen = _singleton_guard("demo.py::test_version")
        next(gen)
        li.version._current_version = object()
        with pytest.raises(pytest.fail.Exception) as excinfo:
            next(gen)
        assert "utils.version._current_version" in str(excinfo.value)
