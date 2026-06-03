"""
DSAR (Data Subject Access Request) mixin - Data export route handlers.
"""

from typing import Optional

from fastapi import HTTPException, Depends
from fastapi.responses import StreamingResponse

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SuccessResponse
from src.api.schemas.dsar import (
    DSARRequestBody,
    DSARRequestResponse,
    DSARRequestListResponse,
)
from src.core.database.cache.rate_limit import check_rate_limit
from src.core.dsar import (
    request_data_export as dsar_request_export,
    get_user_requests,
    get_request_status,
    cancel_request as dsar_cancel_request,
    get_export_file,
)


class DataExportMixin:
    def _verify_password(self, user_id: int, password: str) -> bool:
        auth = api.get_auth()
        if not auth:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Auth module not available"}},
            )

        user = auth.get_user(user_id)
        if not user:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )

        # The User model's password_hash is not populated by get_user()
        # (it is only set on specific operations like login). Query the
        # hash directly from the database so DSAR verification works
        # without requiring a prior login in the same process.
        password_hash: Optional[str] = getattr(user, "password_hash", None)
        if not password_hash:
            db = api.get_db()
            if db:
                row = db.fetch_one(
                    "SELECT password_hash FROM auth_users WHERE id = ?",
                    (user_id,),
                )
                if row:
                    password_hash = (
                        row["password_hash"] if isinstance(row, dict) else row[0]
                    )

        if not password_hash:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Password not set"}},
            )

        from src.utils.encryption import verify_password

        if not verify_password(password, password_hash):
            return False
        return True

    def _check_dsar_rate_limit(self, user_id: int) -> None:
        rate_limit_key = f"dsar:user:{user_id}"
        allowed, remaining = check_rate_limit(
            rate_limit_key, limit=1, window_seconds=86400
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "code": 429,
                        "message": "Data export request limit exceeded. Please wait 24 hours between requests.",
                    }
                },
            )

    async def request_data_export(
        self,
        body: DSARRequestBody,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> SuccessResponse:
        auth = api.get_auth()
        if not auth:
            logger.error("Auth module not available")
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Auth module not available"}},
            )

        user_id = int(current_user.user_id)

        if not self._verify_password(user_id, body.password):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Incorrect password",
                    }
                },
            )

        self._check_dsar_rate_limit(user_id)

        try:
            format_value = body.format if body.format else "json"
            dsar_request_export(user_id, format=format_value)

            logger.info(
                f"User {user_id} requested data export in {format_value} format"
            )
            return SuccessResponse(
                success=True,
                message="Data export request submitted successfully",
            )
        except Exception as e:
            logger.error(
                f"Failed to request data export for user {user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )

    async def get_data_export_requests(
        self,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> DSARRequestListResponse:
        try:
            user_id = int(current_user.user_id)
            requests = get_user_requests(user_id)

            request_responses = []
            for req in requests:
                request_responses.append(
                    DSARRequestResponse(
                        id=req["id"],
                        status=req["status"],
                        requested_at=req["requested_at"],
                        completed_at=req.get("completed_at"),
                        expires_at=req.get("expires_at"),
                        format=req.get("format", "json"),
                        file_size_bytes=req.get("file_size_bytes"),
                        checksum=req.get("checksum"),
                    )
                )

            return DSARRequestListResponse(requests=request_responses)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to get data export requests for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )

    async def get_data_export_status(
        self,
        request_id: str,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> DSARRequestResponse:
        try:
            user_id = int(current_user.user_id)
            req_id = int(request_id)

            request_data = get_request_status(req_id, user_id)
            if not request_data:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Request not found"}},
                )

            return DSARRequestResponse(
                id=request_data["id"],
                status=request_data["status"],
                requested_at=request_data["requested_at"],
                completed_at=request_data.get("completed_at"),
                expires_at=request_data.get("expires_at"),
                format=request_data.get("format", "json"),
                file_size_bytes=request_data.get("file_size_bytes"),
                checksum=request_data.get("checksum"),
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request ID"}},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to get data export status for request {request_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )

    async def cancel_data_export_request(
        self,
        request_id: str,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> SuccessResponse:
        try:
            user_id = int(current_user.user_id)
            req_id = int(request_id)

            request_data = get_request_status(req_id, user_id)
            if not request_data:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Request not found"}},
                )

            if request_data["status"] not in ("pending", "approved"):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Only pending or approved requests can be cancelled",
                        }
                    },
                )

            dsar_cancel_request(req_id, user_id)

            logger.info(f"User {user_id} cancelled data export request {req_id}")
            return SuccessResponse(
                success=True,
                message="Data export request cancelled",
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request ID"}},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to cancel data export request {request_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )

    async def download_data_export(
        self,
        request_id: str,
        current_user: TokenInfo = Depends(get_current_user),
    ):
        try:
            user_id = int(current_user.user_id)
            req_id = int(request_id)

            request_data = get_request_status(req_id, user_id)
            if not request_data:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Request not found"}},
                )

            if request_data["status"] != "ready":
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Export is not ready for download",
                        }
                    },
                )

            from time import time

            expires_at = request_data.get("expires_at")
            if expires_at and expires_at < int(time()):
                raise HTTPException(
                    status_code=410,
                    detail={
                        "error": {
                            "code": 410,
                            "message": "Download link has expired",
                        }
                    },
                )

            export_file = get_export_file(req_id, user_id)
            if not export_file:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Export file not found"}},
                )

            storage_path = export_file.get("storage_path")
            if not storage_path:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": {
                            "code": 404,
                            "message": "Export file path not available",
                        }
                    },
                )

            from src.core.dsar.export_formats import ExportFormatGenerator

            generator = ExportFormatGenerator()
            try:
                stream, size = generator.retrieve_stream(storage_path)
            except Exception as e:
                logger.error(
                    f"Failed to open export stream for {storage_path}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {
                            "code": 500,
                            "message": "Failed to read export from storage",
                        }
                    },
                )

            export_format = export_file.get("format", "json")
            media_type = (
                "application/zip" if export_format == "zip" else "application/json"
            )
            filename = f"dsar_export_{req_id}.{export_format}"
            checksum = export_file.get("checksum", "")

            logger.info(
                f"User {user_id} downloading DSAR export {req_id} "
                f"({size} bytes, {export_format}, sha256={checksum[:16]}...)"
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(size),
                "X-DSAR-Checksum": checksum,
                "X-DSAR-Request-Id": str(req_id),
            }

            return StreamingResponse(
                stream,
                media_type=media_type,
                headers=headers,
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request ID"}},
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to download data export {request_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": str(e)}},
            )
