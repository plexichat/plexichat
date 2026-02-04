
from fastapi import Request
import utils.config as config

def get_client_ip(request: Request) -> str:
    """
    Extract IP address considering trusted proxies configuration.
    
    Consolidated logic for consistent IP extraction across the application.
    Hardened to prevent X-Forwarded-For spoofing.
    """
    # Direct client IP from the socket
    direct_ip = request.client.host if request.client else "127.0.0.1"
    
    trust_x_forwarded = config.get("api.trust_x_forwarded_for", False)
    trusted_proxies = config.get("api.trusted_proxies", [])
    
    # If we don't trust the header, always return the direct socket IP
    if not trust_x_forwarded:
        return direct_ip
        
    # Security: If trust is enabled but no proxies are defined, this is a misconfiguration
    if not trusted_proxies:
        import utils.logger as logger
        logger.warning("api.trust_x_forwarded_for is enabled but api.trusted_proxies is empty. Ignoring header for security.")
        return direct_ip

    # If the direct client is trusted, check X-Forwarded-For
    trusted_proxies_set = set(trusted_proxies)
    if direct_ip in trusted_proxies_set or "*" in trusted_proxies_set:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # The header can contain multiple IPs: client, proxy1, proxy2...
            # We take the first one as the original client
            parts = [p.strip() for p in forwarded.split(",")]
            if parts:
                return parts[0]

    return direct_ip
