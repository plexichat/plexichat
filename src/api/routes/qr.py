"""
QR code generation endpoints.
"""

import io
from typing import Optional
import qrcode
from fastapi import APIRouter, Query, Response, HTTPException
from fastapi.responses import Response

router = APIRouter()

@router.get("/qr", tags=["Utilities"])
async def generate_qr(
    data: str = Query(..., description="The data to encode in the QR code"),
    size: str = Query("200x200", description="Size of the image in pixels (e.g., 200x200)"),
    format: str = Query("png", description="Image format (png or jpg)")
):
    """
    Generate a QR code image locally.
    
    This replaces external services like qrserver.com for better privacy and reliability.
    """
    if not data:
        raise HTTPException(status_code=400, detail="Data is required")

    try:
        width, height = map(int, size.split('x'))
    except (ValueError, AttributeError):
        width, height = 200, 200

    # Limit size to reasonable values
    width = max(10, min(1000, width))
    height = max(10, min(1000, height))

    # Create QR code
    qr = qrcode.QRCode(
        version=None,  # Automatically determine version
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    # Resize to requested dimensions
    img = img.resize((width, height))
    
    img_io = io.BytesIO()
    save_format = 'PNG' if format.lower() == 'png' else 'JPEG'
    img.save(img_io, save_format)
    img_io.seek(0)
    
    content_type = 'image/png' if save_format == 'PNG' else 'image/jpeg'
    return Response(content=img_io.getvalue(), media_type=content_type)
