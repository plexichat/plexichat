"""Bounded pagination and filter strings on application listings."""

import inspect

import pytest

from src.api.routes.applications import (
    MAX_APPLICATION_FILTER_LENGTH,
    MAX_APPLICATION_OFFSET,
    MAX_APPLICATION_PAGE_SIZE,
    list_applications,
)
from src.core.applications.manager.application_crud import AppCRUDMixin
from src.core.security.hardening import HardeningViolation


class TestRouteBounds:
    def test_query_parameters_declared(self):
        params = inspect.signature(list_applications).parameters
        assert params["limit"].default == 50
        assert params["offset"].default == 0
        assert params["name_filter"].default is None
        source = inspect.getsource(list_applications)
        assert "Query(ge=1, le=MAX_APPLICATION_PAGE_SIZE)" in source
        assert "Query(ge=0, le=MAX_APPLICATION_OFFSET)" in source
        assert "Query(max_length=MAX_APPLICATION_FILTER_LENGTH)" in source
        assert MAX_APPLICATION_PAGE_SIZE == 100
        assert MAX_APPLICATION_OFFSET >= MAX_APPLICATION_PAGE_SIZE
        assert MAX_APPLICATION_FILTER_LENGTH == 128


class TestManagerPagination:
    def _seed(self, app_manager, test_user):
        for index in range(3):
            app_manager.create_application(
                owner_id=test_user.id, name=f"Bounded App {index}"
            )

    def test_limit_and_offset(self, app_manager, test_user):
        self._seed(app_manager, test_user)
        assert len(app_manager.get_user_applications(test_user.id)) == 3
        page = app_manager.get_user_applications(test_user.id, limit=1)
        assert len(page) == 1
        second = app_manager.get_user_applications(test_user.id, limit=1, offset=1)
        assert second[0].id != page[0].id

    def test_name_filter(self, app_manager, test_user):
        self._seed(app_manager, test_user)
        matches = app_manager.get_user_applications(
            test_user.id, name_filter="Bounded App 1"
        )
        assert [app.name for app in matches] == ["Bounded App 1"]

    def test_wildcards_are_escaped(self, app_manager, test_user):
        self._seed(app_manager, test_user)
        assert app_manager.get_user_applications(test_user.id, name_filter="%") == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"limit": 0},
            {"limit": -1},
            {"limit": MAX_APPLICATION_PAGE_SIZE + 1},
            {"offset": -1},
            {"offset": MAX_APPLICATION_OFFSET + 1},
            {"name_filter": "a" * (MAX_APPLICATION_FILTER_LENGTH + 1)},
            {"name_filter": "bad\nfilter"},
        ],
    )
    def test_rejects_out_of_bounds(self, app_manager, test_user, kwargs):
        with pytest.raises(HardeningViolation):
            app_manager.get_user_applications(test_user.id, **kwargs)

    def test_facade_forwards_pagination(self, monkeypatch):
        import src.core.applications as applications

        captured = {}

        class Stub:
            def get_user_applications(self, user_id, **kwargs):
                captured.update(kwargs)
                return []

        monkeypatch.setattr(applications, "_get_manager", lambda: Stub())
        applications.get_user_applications(1, limit=2, offset=3, name_filter="abc")
        assert captured == {"limit": 2, "offset": 3, "name_filter": "abc"}

    def test_source_uses_limit_offset(self):
        source = inspect.getsource(AppCRUDMixin.get_user_applications)
        assert "LIMIT ? OFFSET ?" in source
        assert "bounded_int" in source
