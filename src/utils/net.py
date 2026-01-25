
from fastapi import Request
import utils.config as config

def get_client_ip(request: Request) -> str:
    """
    Extract IP address considering trusted proxies configuration.
    
    Consolidated logic for consistent IP extraction across the application.
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    
    trust_x_forwarded = config.get("api.trust_x_forwarded_for", False)
    trusted_proxies = set(config.get("api.trusted_proxies", []))
    
    # If the direct client is trusted, check X-Forwarded-For
    # Support '*' as a wildcard to trust all proxies (dangerous, should warn in logs)
    if trust_x_forwarded and (client_ip in trusted_proxies or "*" in trusted_proxies):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # First IP in the list is the original client
            return forwarded.split(",")[0].strip()

    return client_ip
