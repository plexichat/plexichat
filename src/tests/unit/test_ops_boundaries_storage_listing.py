"""Regression tests for bounded storage-file listing."""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.storage_quota.manager import StorageQuotaManager


class _FakeDB:
    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, params))
        return [{"id": 1, "filename": "a"}]


def _manager():
    return StorageQuotaManager(_FakeDB())


def test_listing_is_paginated_and_escape_prefixed():
    manager = _manager()
    result = manager.list_user_files(1, limit=25, offset=10, path_prefix="uploads/2024")
    assert result["limit"] == 25
    assert result["offset"] == 10
    sql, params = manager._db.calls[-1]
    assert "LIMIT ? OFFSET ?" in sql
    assert "storage_path LIKE ? ESCAPE '\\'" in sql
    assert params[1].endswith("%")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 10**9},
        {"offset": -1},
        {"path_prefix": "../etc"},
        {"path_prefix": "/absolute/path"},
        {"path_prefix": "a/../../b"},
        {"path_prefix": "x" * 513},
        {"path_prefix": "a\x00b"},
    ],
)
def test_listing_rejects_unbounded_or_unsafe_filters(kwargs):
    with pytest.raises(HardeningViolation):
        _manager().list_user_files(1, **kwargs)


def _constraint(func, name, kind):
    import inspect
    import typing

    from fastapi.params import Query as QueryParam

    # A bound may be declared either as a ``Query(...)`` default or in an
    # ``Annotated[..., Query(...)]`` annotation; FastAPI enforces both, so the
    # probe accepts both instead of pinning one spelling of the declaration.
    param = inspect.signature(func).parameters[name]
    annotation = typing.get_type_hints(func, include_extras=True).get(
        name, param.annotation
    )
    candidates = [param.default]
    if typing.get_origin(annotation) is typing.Annotated:
        candidates.extend(annotation.__metadata__)
    for candidate in candidates:
        if not isinstance(candidate, QueryParam):
            continue
        for meta in candidate.metadata:
            if type(meta).__name__ == kind:
                return meta
    raise AssertionError(f"{name} has no {kind} constraint")


def test_route_declares_bounded_query_parameters():
    from src.api.routes import storage as storage_routes

    func = storage_routes.list_my_storage_files
    assert _constraint(func, "limit", "Le").le == 200
    assert _constraint(func, "limit", "Ge").ge == 1
    assert _constraint(func, "offset", "Le").le == 1_000_000
    assert _constraint(func, "path", "MaxLen").max_length == 512
