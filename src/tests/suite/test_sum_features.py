"""Tests for persisted PlexiSum client features (charts/pivots/sparklines).

``sum_features`` stores client-authored definitions so charts, pivot tables,
and sparklines survive reloads and can be shared. These tests pin the CRUD
surface, the feature-type allow-list, the definition size/type bounds, and the
mutation authorization boundary.
"""

import pytest

from src.core.suite.sum.manager import (
    SumManager,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def test_feature_crud_round_trip(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Charts")
    sheet_id = workbook["sheets"][0]["id"]

    chart = manager.create_feature(
        owner.id,
        int(workbook["id"]),
        "chart",
        {"type": "column", "title": "Sales", "range": "A1:B10"},
        int(sheet_id),
    )
    assert chart["feature_type"] == "chart"
    assert chart["definition"]["title"] == "Sales"

    pivot = manager.create_feature(
        owner.id,
        int(workbook["id"]),
        "pivot",
        {"rows": ["Region"], "values": ["Revenue"]},
    )
    sparkline = manager.create_feature(
        owner.id, int(workbook["id"]), "sparkline", {"type": "line"}
    )

    listed = manager.list_features(owner.id, int(workbook["id"]))
    assert {f["id"] for f in listed} == {chart["id"], pivot["id"], sparkline["id"]}

    # Filter by feature type.
    charts = manager.list_features(owner.id, int(workbook["id"]), "chart")
    assert [f["id"] for f in charts] == [chart["id"]]

    # Update the definition.
    updated = manager.update_feature(
        owner.id, int(workbook["id"]), int(chart["id"]), definition={"type": "line"}
    )
    assert updated["definition"]["type"] == "line"

    # Delete.
    manager.delete_feature(owner.id, int(workbook["id"]), int(chart["id"]))
    remaining = manager.list_features(owner.id, int(workbook["id"]))
    assert chart["id"] not in {f["id"] for f in remaining}


def test_feature_type_must_be_known(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Bad type")
    with pytest.raises(SumValidationError):
        manager.create_feature(owner.id, int(workbook["id"]), "script", {})
    with pytest.raises(SumValidationError):
        manager.list_features(owner.id, int(workbook["id"]), "script")


def test_feature_definition_must_be_object(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Bad definition")
    with pytest.raises(SumValidationError):
        manager.create_feature(owner.id, int(workbook["id"]), "chart", "not-an-object")
    with pytest.raises(SumValidationError):
        manager.create_feature(owner.id, int(workbook["id"]), "chart", [1, 2, 3])


def test_feature_definition_size_bounded(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Huge definition")
    huge = {"blob": "x" * (300 * 1024)}
    with pytest.raises(SumValidationError):
        manager.create_feature(owner.id, int(workbook["id"]), "chart", huge)


def test_feature_mutation_requires_edit_access(db, two_users, server_manager):
    owner, other = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Private")
    with pytest.raises(SumPermissionError):
        manager.create_feature(other.id, int(workbook["id"]), "chart", {})
    with pytest.raises(SumPermissionError):
        manager.list_features(other.id, int(workbook["id"]))


def test_update_missing_feature_is_not_found(db, two_users, server_manager):
    owner, _ = two_users
    manager = _manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Missing")
    with pytest.raises(SumNotFoundError):
        manager.update_feature(
            owner.id, int(workbook["id"]), 999_999, definition={"type": "line"}
        )
    with pytest.raises(SumNotFoundError):
        manager.delete_feature(owner.id, int(workbook["id"]), 999_999)
