# TLS module

The TLS module generates and manages self-signed TLS certificates for
development and testing environments. It allows Plexichat to serve HTTPS
without manual certificate setup.

## Features

- Generates a self-signed certificate on startup when none exists.
- Checks for certificate expiration and regenerates before expiry.
- Includes `localhost`, `127.0.0.1`, and `::1` in the Subject Alternative
  Names (SANs).
- Stores private keys with restrictive file permissions (`0600`).
- Supports configurable paths, validity period, and hostname.

## Configuration

Add the following to `config.yaml`:

```yaml
tls:
  # Enable automatic self-signed certificate generation.
  auto_generate_self_signed: false

  # Certificate file paths (default: ~/.plexichat/certs/).
  cert_path: ~/.plexichat/certs/server.crt
  key_path: ~/.plexichat/certs/server.key

  # Hostname embedded in the certificate CN and SANs.
  # On auto-renewal the original hostname and SANs are preserved.
  # Valid range for cert_days: 1–3650. Invalid values abort startup.
  # Missing or unreadable certificates when TLS is enabled also
  # abort startup rather than falling back to plain HTTP.
  hostname: localhost
  cert_days: 365
```

## Usage

### Programmatic usage

```python
from src.core import tls

# Ensure certificates exist (generates if needed).
cert_path, key_path = tls.ensure_certificates()

# Check whether TLS is enabled.
if tls.is_tls_enabled():
    ssl_config = tls.get_tls_config()
    # Pass ssl_config to uvicorn.
```

### Server startup

When TLS is enabled, the server performs the following steps:

1. Checks whether certificates exist at the configured paths.
2. Validates certificate expiration.
3. Generates new self-signed certificates if needed.
4. Starts uvicorn with the SSL configuration.

## Certificate details

Generated certificates use the following properties:

- **Key size:** 2048-bit RSA
- **Signature algorithm:** SHA-256
- **Subject Alternative Names:**
  - `DNS: localhost`
  - `DNS:` configured hostname
  - `IP: 127.0.0.1`
  - `IP: ::1`

## Validation cache

`validate_certificate_file` reuses the result of a **successful** chain /
hostname / pin validation through a small, thread-safe LRU + TTL cache
(`validation_cache.py`, 64 entries / 60 seconds by default). This avoids
repeating expensive signature checks on every startup or worker boot.

Invalidation is fail-closed:

- the key includes the SHA-256 of the certificate bytes, so rotated material
  misses instead of resuming an old verdict;
- hostname and pins are part of the key, so another domain cannot reuse a
  validation performed for a different one;
- entries expire on a monotonic TTL and at the earliest certificate `notAfter`
  in the chain, so a cached result cannot outlive the certificate;
- failed validations are never cached, and `ensure_certificates()` clears the
  cache after regenerating a certificate.

Observability is available through `validation_cache_stats()`; operators can
force a cold validation with `invalidate_validation_cache()`.

## File locations

Default certificate storage:

```
~/.plexichat/
  certs/
    server.crt    # Certificate (PEM format)
    server.key    # Private key (PEM format, mode 0600)
```

## Dependencies

Requires the `cryptography` library:

```bash
pip install cryptography
```

## Security warning

Self-signed certificates are **not** suitable for production use:

- Browsers display security warnings.
- Clients must explicitly trust the certificate.
- No chain of trust to a Certificate Authority exists.

For production environments, use certificates from one of the following:

- [Let's Encrypt](https://letsencrypt.org/) (free, automated)
- Commercial Certificate Authorities
- An internal CA (for intranet use)
