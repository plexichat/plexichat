"""Dedicated self-tests for stateful PlexiSuite domains.

The generic OpenAPI loop is intentionally not used for these routes: suite
resources require IDs and revisions returned by earlier requests.
"""

import time
from typing import Any, Dict, Optional

import utils.logger as logger

from .base import EndpointTesterBase

from ...db_guard import sanitize_error


class SuiteMixin(EndpointTesterBase):
    """Exercise real Stash and Sum resources in dependency order."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._suite_capabilities: Dict[str, str] = {}

    def _expected_capability_denial(self, response: Any, label: str) -> Optional[str]:
        """Return the gated capability when a disabled feature denies a probe.

        A 403 is only expected for the capability-gated Stash/Sum operations
        when the response explicitly carries a known capability state. Other
        authorization failures remain ordinary self-test failures.
        """
        capability = None
        if label.startswith("stash_"):
            capability = "plexistash"
        elif label.startswith("sum_"):
            capability = "plexisum"
        if (
            capability is None
            or response.status_code != 403
            or self._suite_capabilities.get(capability) in (None, "available")
        ):
            return None
        try:
            body = response.json()
            error = body.get("error") or body.get("detail", {}).get("error", {})
            state = error.get("state") if isinstance(error, dict) else None
            if state in {
                "disabled_by_config",
                "disabled_by_license",
                "dependency_missing",
                "misconfigured",
            } and state == self._suite_capabilities.get(capability):
                return f"{capability}:{state}"
        except (AttributeError, TypeError, ValueError):
            pass
        return None

    def _suite_result(
        self,
        method: str,
        path: str,
        response: Any,
        started: float,
        label: str,
    ) -> bool:
        expected_denial = self._expected_capability_denial(response, label)
        success = (200 <= response.status_code < 300) or expected_denial is not None
        self.ctx.results.append(
            {
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": (time.time() - started) * 1000,
                "success": success,
                "label": label,
            }
        )
        if expected_denial:
            self.ctx.results[-1]["expected_denial"] = expected_denial
            logger.info(
                "%s EXPECTED DENIAL -> %s (%s)",
                label,
                response.status_code,
                expected_denial,
            )
        elif success:
            logger.info("%s PASSED -> %s", label, response.status_code)
        else:
            logger.warning(
                "%s -> %s: %s", label, response.status_code, response.text[:300]
            )
        return success

    def _request_suite(
        self,
        method: str,
        path: str,
        label: str,
        *,
        json: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        started = time.time()
        try:
            response = self.ctx.session.request(
                method,
                f"{self.ctx.base_url}{path}",
                json=json,
                timeout=5,
            )
        except Exception as exc:
            self.ctx.results.append(
                {
                    "method": method,
                    "path": path,
                    "status_code": 0,
                    "duration_ms": (time.time() - started) * 1000,
                    "success": False,
                    "label": label,
                    "error": sanitize_error(exc),
                }
            )
            logger.warning("%s exception: %s", label, exc)
            return None
        self._suite_result(method, path, response, started, label)
        return response

    @staticmethod
    def _json_body(response: Any) -> Dict[str, Any]:
        try:
            body = response.json()
        except (TypeError, ValueError):
            return {}
        return body if isinstance(body, dict) else {}

    def test_suite_domains(self) -> None:
        """Create and exercise one real personal Stash item and workbook."""
        if not self.ctx.standalone_mode:
            return
        if not self.ctx.test_user_id:
            logger.debug("Skipping PlexiSuite tests: no authenticated test user")
            return

        self._test_registry()
        self._test_stash()
        self._test_sum()

    def _test_registry(self) -> None:
        """Verify every authenticated universal-home endpoint is registered."""
        checks = (
            ("GET", "/api/v1/suite/apps", "suite_list_apps"),
            ("GET", "/api/v1/suite/home/recent?limit=10", "suite_recent"),
            ("GET", "/api/v1/suite/home/pinned?limit=10", "suite_pinned"),
            ("GET", "/api/v1/suite/home/shared?limit=10", "suite_shared"),
            ("GET", "/api/v1/suite/home/activity?limit=10", "suite_activity"),
            ("GET", "/api/v1/suite/search?q=selftest&limit=10", "suite_search"),
        )
        for method, path, label in checks:
            response = self._request_suite(method, path, label)
            if (
                label == "suite_list_apps"
                and response is not None
                and response.status_code == 200
            ):
                for app in self._json_body(response).get("apps", []):
                    app_id = app.get("id")
                    availability = app.get("availability")
                    if app_id in {"plexistash", "plexisum"} and availability:
                        self._suite_capabilities[app_id] = str(availability)

    def _test_stash(self) -> None:
        collection_path = "/api/v1/suite/stash/collections"
        collection_response = self._request_suite(
            "POST",
            collection_path,
            "stash_create_collection",
            json={"name": "Self-test collection"},
        )
        collection_id = None
        if (
            collection_response is not None
            and 200 <= collection_response.status_code < 300
        ):
            collection_id = self._json_body(collection_response).get("id")
            self.ctx.test_stash_collection_id = (
                str(collection_id) if collection_id else None
            )

        item_payload: Dict[str, Any] = {
            "item_type": "note",
            "title": "Self-test Stash note",
            "scope": "personal",
            "metadata": {"content": "PlexiStash self-test"},
            "tags": ["selftest"],
        }
        if collection_id:
            item_payload["collection_id"] = collection_id

        item_path = "/api/v1/suite/stash/items"
        item_response = self._request_suite(
            "POST", item_path, "stash_create_item", json=item_payload
        )
        if item_response is None:
            return
        if not 200 <= item_response.status_code < 300:
            if self._suite_capabilities.get("plexistash") != "available":
                self._probe_stash_denied_routes()
            return

        item_id = self._json_body(item_response).get("id")
        if not item_id:
            logger.warning("stash_create_item returned no item id")
            return
        self.ctx.test_stash_item_id = str(item_id)

        self._request_suite(
            "GET",
            f"{item_path}/{item_id}",
            "stash_get_item",
        )
        self._request_suite(
            "GET",
            f"{item_path}?limit=10&scope=personal",
            "stash_list_items",
        )
        self._request_suite(
            "PATCH",
            f"{item_path}/{item_id}",
            "stash_update_item",
            json={"title": "Updated self-test note"},
        )
        self._request_suite(
            "POST",
            f"{item_path}/{item_id}/pin",
            "stash_pin_item",
            json={"pinned": True},
        )
        self._request_suite(
            "POST",
            f"{item_path}/{item_id}/versions",
            "stash_create_version",
            json={
                "content_type": "text/plain",
                "size_bytes": 22,
                "checksum": "selftest-checksum",
            },
        )
        self._request_suite(
            "GET",
            f"{item_path}/{item_id}/versions?limit=10",
            "stash_list_versions",
        )
        self._request_suite(
            "DELETE",
            f"{item_path}/{item_id}",
            "stash_delete_item",
        )
        self._request_suite(
            "POST",
            f"{item_path}/{item_id}/restore",
            "stash_restore_item",
        )

    def _probe_stash_denied_routes(self) -> None:
        """Exercise every Stash route when its capability is unavailable."""
        item_path = "/api/v1/suite/stash/items/0"
        probes = (
            ("GET", "/api/v1/suite/stash/items", "stash_probe_list_items"),
            ("GET", item_path, "stash_probe_get_item"),
            ("PATCH", item_path, "stash_probe_update_item"),
            ("POST", f"{item_path}/pin", "stash_probe_pin_item"),
            ("DELETE", item_path, "stash_probe_delete_item"),
            ("POST", f"{item_path}/restore", "stash_probe_restore_item"),
            ("GET", f"{item_path}/versions", "stash_probe_list_versions"),
            ("POST", f"{item_path}/versions", "stash_probe_create_version"),
            ("POST", f"{item_path}/grants", "stash_probe_grant_item"),
            ("POST", f"{item_path}/download-ticket", "stash_probe_ticket"),
            ("DELETE", "/api/v1/suite/stash/grants/0", "stash_probe_revoke_grant"),
            ("GET", "/api/v1/suite/stash/collections", "stash_probe_list_collections"),
            (
                "POST",
                "/api/v1/suite/stash/collections",
                "stash_probe_create_collection",
            ),
            (
                "POST",
                "/api/v1/suite/stash/download-ticket/redeem",
                "stash_probe_redeem",
            ),
        )
        payloads = {
            "PATCH": {"title": "probe"},
            "POST": {"pinned": False},
        }
        for method, path, label in probes:
            body = None
            if method == "PATCH":
                body = payloads[method]
            elif label == "stash_probe_create_version":
                body = {"content_type": "text/plain"}
            elif label == "stash_probe_grant_item":
                body = {"subject_id": 0, "permissions": ["view_metadata"]}
            elif label == "stash_probe_redeem":
                body = {"ticket": "x" * 32}
            elif label == "stash_probe_create_collection":
                body = {"name": "capability-probe"}
            elif label == "stash_probe_pin_item":
                body = {"pinned": False}
            self._request_suite(method, path, label, json=body)

    def _test_sum(self) -> None:
        workbook_path = "/api/v1/suite/sum/workbooks"
        workbook_response = self._request_suite(
            "POST",
            workbook_path,
            "sum_create_workbook",
            json={"title": "Self-test workbook", "scope": "personal"},
        )
        if workbook_response is None:
            return
        if not 200 <= workbook_response.status_code < 300:
            if self._suite_capabilities.get("plexisum") != "available":
                self._probe_sum_denied_routes()
            return

        workbook = self._json_body(workbook_response)
        workbook_id = workbook.get("id")
        sheets = workbook.get("sheets")
        sheet_id = sheets[0].get("id") if isinstance(sheets, list) and sheets else None
        if not workbook_id or not sheet_id:
            logger.warning("sum_create_workbook returned no workbook/sheet id")
            return
        self.ctx.test_sum_workbook_id = str(workbook_id)
        self.ctx.test_sum_sheet_id = str(sheet_id)

        self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}",
            "sum_get_workbook",
        )
        operation_path = f"{workbook_path}/{workbook_id}/sheets/{sheet_id}/operations"
        self._request_suite(
            "POST",
            operation_path,
            "sum_apply_operation",
            json={
                "operation_id": f"plexichat-selftest-op-{time.time_ns()}",
                "operation_type": "set_cell",
                "base_revision": 0,
                "cells": [
                    {
                        "row": 0,
                        "column": 0,
                        "value_type": "text",
                        "value": "PlexiSum self-test",
                    }
                ],
            },
        )
        viewport_path = (
            f"{workbook_path}/{workbook_id}/sheets/{sheet_id}/viewport"
            "?row_start=0&row_end=5&column_start=0&column_end=5"
        )
        self._request_suite("GET", viewport_path, "sum_read_viewport")

        # Production data layer: replay, snapshots, sheet management, CSV,
        # sharing grants, and range permissions.
        self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}/operations?after_revision=0&limit=100",
            "sum_replay_operations",
        )
        snapshot_response = self._request_suite(
            "POST",
            f"{workbook_path}/{workbook_id}/snapshots",
            "sum_create_snapshot",
            json={},
        )
        snapshot_id = None
        if snapshot_response is not None and 200 <= snapshot_response.status_code < 300:
            snapshot_id = self._json_body(snapshot_response).get("id")
        self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}/snapshots?limit=10",
            "sum_list_snapshots",
        )
        if snapshot_id:
            self._request_suite(
                "POST",
                f"{workbook_path}/{workbook_id}/snapshots/{snapshot_id}/restore",
                "sum_restore_snapshot",
            )
        sheet_response = self._request_suite(
            "POST",
            f"{workbook_path}/{workbook_id}/sheets",
            "sum_create_sheet",
            json={"name": "Data sheet"},
        )
        second_sheet_id = None
        if sheet_response is not None and 200 <= sheet_response.status_code < 300:
            second_sheet_id = self._json_body(sheet_response).get("id")
        if second_sheet_id:
            self._request_suite(
                "PATCH",
                f"{workbook_path}/{workbook_id}/sheets/{second_sheet_id}/rename",
                "sum_rename_sheet",
                json={"name": "Renamed sheet"},
            )
        csv_response = self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}/sheets/{sheet_id}/csv",
            "sum_export_csv",
        )
        if csv_response is not None and 200 <= csv_response.status_code < 300:
            workbook = self._json_body(csv_response)
            current = workbook.get("content", "")
            if current:
                # Re-import the exported CSV as a fresh revision to exercise
                # the bounded import path end to end. A null base revision
                # targets the workbook's current revision (the workbook has
                # advanced past 0 by the operations above).
                imported = self._request_suite(
                    "POST",
                    f"{workbook_path}/{workbook_id}/sheets/{sheet_id}/csv",
                    "sum_import_csv",
                    json={"content": current, "base_revision": None},
                )
                if imported is not None and 200 <= imported.status_code < 300:
                    self.ctx.test_sum_operation_id = self._json_body(imported).get(
                        "operation_id"
                    )
        file_response = self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}/file",
            "sum_download_file",
        )
        if file_response is not None and 200 <= file_response.status_code < 300:
            file_body = self._json_body(file_response)
            file_content = file_body.get("content")
            if file_content and file_content != "":
                import base64

                try:
                    decoded = base64.b64decode(file_content, validate=False)
                    self._request_suite(
                        "POST",
                        f"{workbook_path}/{workbook_id}/file",
                        "sum_upload_file",
                        json={
                            "content": base64.b64encode(decoded).decode(),
                            "base_revision": None,
                        },
                    )
                except Exception:
                    logger.warning("sum file round-trip skipped: base64 decode failed")
        self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}/grants",
            "sum_list_grants",
        )
        grant_response = self._request_suite(
            "POST",
            f"{workbook_path}/{workbook_id}/grants",
            "sum_grant_workbook",
            json={"subject_id": 2, "permissions": ["view"], "expires_at": None},
        )
        grant_id = None
        if grant_response is not None and 200 <= grant_response.status_code < 300:
            grant_id = self._json_body(grant_response).get("id")
        if grant_id:
            self._request_suite(
                "DELETE",
                f"/api/v1/suite/sum/grants/{grant_id}",
                "sum_revoke_grant",
            )
        self._request_suite(
            "GET",
            f"{workbook_path}/{workbook_id}/sheets/{sheet_id}/range-permissions",
            "sum_list_range_permissions",
        )
        range_response = self._request_suite(
            "POST",
            f"{workbook_path}/{workbook_id}/sheets/{sheet_id}/range-permissions",
            "sum_set_range_permission",
            json={
                "row_start": 0,
                "row_end": 5,
                "column_start": 0,
                "column_end": 5,
                "permission": "read_only",
            },
        )
        range_id = None
        if range_response is not None and 200 <= range_response.status_code < 300:
            range_id = self._json_body(range_response).get("id")
        if range_id:
            self._request_suite(
                "DELETE",
                f"{workbook_path}/{workbook_id}/range-permissions/{range_id}",
                "sum_remove_range_permission",
            )
        self._request_suite(
            "DELETE",
            f"{workbook_path}/{workbook_id}",
            "sum_delete_workbook",
        )
        self._request_suite(
            "POST",
            f"{workbook_path}/{workbook_id}/restore",
            "sum_restore_workbook",
        )

    def _probe_sum_denied_routes(self) -> None:
        """Exercise every Sum route when its capability is unavailable."""
        workbook_path = "/api/v1/suite/sum/workbooks/0"
        probes = (
            ("GET", "/api/v1/suite/sum/workbooks", "sum_probe_list_workbooks"),
            ("GET", workbook_path, "sum_probe_get_workbook"),
            ("DELETE", workbook_path, "sum_probe_delete_workbook"),
            ("POST", f"{workbook_path}/restore", "sum_probe_restore_workbook"),
            ("GET", f"{workbook_path}/sheets/0/viewport", "sum_probe_viewport"),
            ("POST", f"{workbook_path}/sheets/0/operations", "sum_probe_operation"),
            ("GET", f"{workbook_path}/operations", "sum_probe_replay"),
            ("POST", f"{workbook_path}/snapshots", "sum_probe_snapshot"),
            ("POST", f"{workbook_path}/sheets", "sum_probe_create_sheet"),
            ("PATCH", f"{workbook_path}/sheets/0/rename", "sum_probe_rename_sheet"),
            ("GET", f"{workbook_path}/sheets/0/csv", "sum_probe_export_csv"),
            ("POST", f"{workbook_path}/sheets/0/csv", "sum_probe_import_csv"),
            ("GET", f"{workbook_path}/file", "sum_probe_download_file"),
            ("POST", f"{workbook_path}/file", "sum_probe_upload_file"),
            ("GET", f"{workbook_path}/grants", "sum_probe_list_grants"),
            ("POST", f"{workbook_path}/grants", "sum_probe_grant"),
            (
                "GET",
                f"{workbook_path}/sheets/0/range-permissions",
                "sum_probe_list_ranges",
            ),
            (
                "POST",
                f"{workbook_path}/sheets/0/range-permissions",
                "sum_probe_set_range",
            ),
        )
        for method, path, label in probes:
            body = None
            if label == "sum_probe_operation":
                body = {
                    "operation_id": "suite-capability-probe",
                    "operation_type": "set_cell",
                    "base_revision": 0,
                    "cells": [
                        {"row": 0, "column": 0, "value_type": "text", "value": "probe"}
                    ],
                }
            elif label == "sum_probe_snapshot":
                body = {}
            elif label == "sum_probe_create_sheet":
                body = {"name": "capability-probe"}
            elif label == "sum_probe_rename_sheet":
                body = {"name": "capability-probe"}
            elif label == "sum_probe_import_csv":
                body = {"content": "a,b\n1,2\n", "base_revision": 0}
            elif label == "sum_probe_upload_file":
                body = {"content": "bm90LWEtcGxleGlzdW0tZmlsZQ=="}
            elif label == "sum_probe_grant":
                body = {"subject_id": 0, "permissions": ["view"]}
            elif label == "sum_probe_set_range":
                body = {
                    "row_start": 0,
                    "row_end": 1,
                    "column_start": 0,
                    "column_end": 1,
                    "permission": "read_only",
                }
            self._request_suite(method, path, label, json=body)
