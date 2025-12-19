# TLS Module

Automatic self-signed TLS certificate generation for PlexiChat server.

## Overview

This module enables automatic generation of self-signed TLS certificates, allowing HTTPS to be enabled without manual certificate setup. This is particularly useful for:

- Development and testing environments
- Voice/video features that require HTTPS (WebRTC)
- Quick local deployments

## Features

- **Auto-Generation**: Creates self-signed certificates on startup if none exist
- **Validity Checking**: Regenerates certificates before they expire
- **SAN Support**: Includes localhost, 127.0.0.1, and ::1 in Subject Alternative Names
- **Secure Storage**: Private keys stored with restrictive permissions
- **Configurable**: Paths, validity period, and hostname are configurable

## Usage

### Configuration

Add to `config.yaml`:

```yaml
tls:
  # Enable automatic self-signed certificate generation
  auto_generate_self_signed: false
  
  # Certificate file paths (default: ~/.plexichat/certs/)
  cert_path: ~/.plexichat/certs/server.crt
  key_path: ~/.plexichat/certs/server.key
  
  # Certificate validity period in days
  cert_days: 365
```

### Programmatic Usage

```python
from src.core import tls

# Ensure certificates exist (generates if needed)
cert_path, key_path = tls.ensure_certificates()

# Check if TLS is enabled
if tls.is_tls_enabled():
    ssl_config = tls.get_tls_config()
    # Use ssl_config with uvicorn
```

### Server Startup

When TLS is enabled, the server will:

1. Check if certificates exist at configured paths
2. Validate certificate expiration
3. Generate new self-signed certificates if needed
4. Start uvicorn with SSL configuration

## Security Warning

Self-signed certificates are **NOT** suitable for production use:

- Browsers will show security warnings
- Clients must explicitly trust the certificate
- No chain of trust to a Certificate Authority

For production, use certificates from:
- Let's Encrypt (free, automated)
- Commercial Certificate Authorities
- Your internal CA certificates (for intranet use)

## Dependencies

Requires the `cryptography` library:

```bash
pip install cryptography
```

## Certificate Details

Generated certificates include:

- **Key Size**: 2048-bit RSA
- **Signature**: SHA-256
- **Subject Alternative Names**:
  - DNS: localhost
  - DNS: configured hostname
  - IP: 127.0.0.1
  - IP: ::1

## File Locations

Default certificate storage:

```
~/.plexichat/
  certs/
    server.crt    # Certificate (PEM format)
    server.key    # Private key (PEM format, mode 0600)
```
