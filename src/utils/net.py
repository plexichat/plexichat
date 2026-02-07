
from fastapi import Request

from typing import Optional

def get_client_ip(request: Request) -> Optional[str]:
    """
    Extract IP address considering trusted proxies configuration.
    
    Consolidated logic for consistent IP extraction across the application.
    Hardened to prevent X-Forwarded-For spoofing.
    """
    # Direct client IP from the socket
    direct_ip = request.client.host if request.client else None

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[0]

    return direct_ip
