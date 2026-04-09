"""
QR code generation endpoints.
"""

import io
from typing import Any, cast

try:
    import qrcode
    from qrcode.image.pure import PyPNGImage
except ImportError:
    qrcode = None  # type: ignore
    PyPNGImage = None  # type: ignore

from fastapi import APIRouter, Query, Response, HTTPException, status
from src.api.schemas.common import ErrorResponse
import utils.logger as logger

router = APIRouter(tags=["Utilities"])


@router.get(
    "/qr",
    summary="Generate QR code",
    responses={
        200: {
            "description": "The generated QR code image",
            "content": {"image/png": {}, "image/jpeg": {}},
        },
        400: {"model": ErrorResponse, "description": "Invalid input data"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def generate_qr(
    data: str = Query(..., description="The data to encode in the QR code"),
    size: str = Query(
        "200x200", description="Size of the image in pixels (e.g., 200x200)"
    ),
    format: str = Query("png", description="Image format (png or jpg)"),
) -> Response:
    """
    Generate a QR code image locally.

    This replaces external services like qrserver.com for better privacy and reliability.
    """
    if not data:
        logger.warning("QR generation requested with no data")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Data is required"}},
        )
    if qrcode is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "QR code dependency missing"}},
        )

    from fastapi.concurrency import run_in_threadpool

    def _generate():
        try:
            try:
                width, height = map(int, size.split("x"))
            except (ValueError, AttributeError):
                width, height = 200, 200

            # Limit size to reasonable values
            width = max(10, min(1000, width))
            height = max(10, min(1000, height))

            # Create QR code
            qr = qrcode.QRCode(  # type: ignore
                version=None,  # Automatically determine version
                error_correction=qrcode.constants.ERROR_CORRECT_L,  # type: ignore
                box_size=10,
                border=4,
            )
            qr.add_data(data)
            qr.make(fit=True)

            img = cast(Any, qr.make_image(fill_color="black", back_color="white"))

            # Resize to requested dimensions if supported (PIL image)
            if hasattr(img, "resize") and callable(getattr(img, "resize", None)):
                img = img.resize((width, height))

            img_io = io.BytesIO()
            save_format = "PNG" if format.lower() == "png" else "JPEG"
            img.save(img_io, save_format)
            return (
                img_io.getvalue(),
                "image/png" if save_format == "PNG" else "image/jpeg",
            )
        except Exception as e:
            logger.error(f"Failed to generate QR code in thread: {e}", exc_info=True)
            raise e

    try:
        image_bytes, content_type = await run_in_threadpool(_generate)
        return Response(
            content=image_bytes,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": str(e)}},
        )
