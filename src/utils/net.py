from fastapi import Request
from typing import Optional, Union, Dict, Any, List
import utils.config as config

def get_client_ip(request: Union[Request, Dict[str, Any]]) -> Optional[str]:
    """
    Extract IP address from a FastAPI Request or ASGI scope.
    Considering trusted proxies configuration.
    
    Consolidated logic for consistent IP extraction across the application.
    Hardened to prevent X-Forwarded-For spoofing.
    """
    if isinstance(request, Request):
        scope = request.scope  # type: ignore
    else:
        scope = request

    # Direct client IP from the socket
    client = scope.get("client")
    direct_ip = client[0] if client else None

    # Get headers from scope
    headers = scope.get("headers", [])
    
    # Function to get header value from ASGI list of tuples
    def get_header(name: str) -> Optional[str]:
        name_bytes = name.lower().encode()
        for k, v in headers:
            if k.lower() == name_bytes:
                return v.decode()
        return None

    trusted_proxies: List[str] = []
    try:
        api_conf = config.get("api", {})
        trusted_proxies = api_conf.get("trusted_proxies", []) or []
    except Exception:
        trusted_proxies = []

    # Only trust X-Forwarded-For if the direct peer is a trusted proxy.
    if direct_ip and direct_ip in trusted_proxies:
        forwarded = get_header("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if parts:
                return parts[0]

    return direct_ip
