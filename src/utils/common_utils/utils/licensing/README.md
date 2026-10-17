# Licensing

Asymmetric cryptography-based license validation for Plexichat. Uses Ed25519 signatures to verify license authenticity, with feature gating for boolean and tiered features, instance identification, and a soft-fail free tier mode.

## Overview

Plexichat is source-available software. The licensing module gates premium features behind cryptographically signed licenses while keeping all base platform functionality available without one.

Key capabilities:

- **Ed25519 signature verification**: Public key is hardcoded; private key is held by the licensing authority only.
- **Multi-source loading**: License from file, base64 environment variable, or default path, tried in priority order.
- **Feature gating**: Boolean on/off flags and tiered features with configuration objects.
- **Resource limits**: Enforceable caps (max users, max servers, etc.) from the license.
- **Free tier**: When no valid license exists, the system runs with premium features disabled and a non-commercial warning.
- **Provisional/test licenses**: CI-only licenses with localhost restriction and client banner, no signature required.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Application                                         │
│  ┌──────────────────────────────────────────────┐    │
│  │  licensing.setup() → LicenseManager           │    │
│  │  ┌───────────┬────────────┬────────────────┐  │    │
│  │  │  Loading  │ Validation │  Feature Check  │  │    │
│  │  │ (file/env │ (Ed25519   │ (bool/tiered)  │  │    │
│  │  │  /default)│  verify)   │                │  │    │
│  │  └───────────┴────────────┴────────────────┘  │    │
│  └──────────────────────────────────────────────┘    │
│                                                      │
│  License sources (priority order):                   │
│  1. Explicit license_path argument                   │
│  2. PLEXICHAT_LICENSE env var (file path)            │
│  3. PLEXICHAT_LICENSE env var (base64)               │
│  4. ~/.plexichat/config/license[.json]               │
│  5. Free tier mode (no license)                      │
└──────────────────────────────────────────────────────┘
```

## Key files

| File | Purpose |
|---|---|
| `__init__.py` | Module facade — `setup()`, `has_feature()`, `get_feature_config()`, re-exports |
| `core.py` | `License`, `LicenseManager`, `LicenseValidationResult`, Ed25519 verification, `sign_license()` |

## Usage

### Setup

```python
import utils.licensing as license

# Standard setup — auto-detects license from env/file/default
is_licensed = license.setup()

# With explicit public key (for testing)
is_licensed = license.setup(public_key_base64="your_key_here")

# With explicit license path
is_licensed = license.setup(license_path="/etc/plexichat/license.json")
```

### Check features

```python
# Boolean feature
if license.has_feature("bond"):
    enable_bond_feature()

# Tiered feature with configuration
config = license.get_feature_config("advanced_automod")
max_rules = config.get("max_rules", 10)
ai_enabled = config.get("ai_enabled", False)

# Base platform features are always available
if license.has_feature("messaging"):
    handle_message()

# "all" meta-feature or type="test" enables everything
if license.has_feature("any_feature_at_all"):
    ...
```

### Resource limits

```python
max_users = license.get_feature_limit("max_users", default=100)
max_servers = license.get_feature_limit("max_servers", default=10)

# Check if a limit is enforced
if license.is_limit_enforced(max_users):
    enforce_user_cap(max_users)
```

### License info

```python
# Check validity
if license.is_valid():
    print(f"Licensed instance: {license.get_instance_id()}")

# Check free tier
if license.is_free_tier():
    print("Running without a license — non-commercial use only")

# Get expiry
expiry = license.get_expiry_timestamp()
if expiry:
    days_left = (expiry - time.time()) / 86400
    print(f"License expires in {days_left:.0f} days")

# Get source
source = license.get_license_source()
# "file:/path/to/license" or "env:b64" or "dict"
```

### Hot-swap license at runtime

```python
# From base64 (admin route)
result = license.apply_license_from_base64(base64_payload)

# From dictionary (CI self-test)
result = license.apply_license_from_dict(license_dict)

# Reload from original source
license.reload_license()
```

## License format

```json
{
    "version": "1.0",
    "type": "standard",
    "instance_id": "plexichat-prod-001",
    "issued_at": 1746292800,
    "expires_at": 1777828800,
    "features": {
        "bond": true,
        "advanced_automod": {"max_rules": 50},
        "sso": false,
        "all": true
    },
    "limits": {
        "max_users": 1000,
        "max_servers": 100
    },
    "allowed_hosts": ["127.0.0.1", "localhost"],
    "signature": "base64_encoded_ed25519_signature"
}
```

| Field | Description |
|---|---|
| `version` | License format version (currently `"1.0"`) |
| `type` | `"standard"`, `"commercial"`, `"enterprise"`, or `"test"` |
| `instance_id` | Unique deployment identifier |
| `features` | Map of feature names to booleans or config objects |
| `limits` | Resource limit caps |
| `allowed_hosts` | Host restriction (test licenses only, defaults to localhost) |
| `signature` | Ed25519 signature over all fields except `signature` itself |

## Environment variables

| Variable | Usage |
|---|---|
| `PLEXICHAT_LICENSE` | License as file path or base64-encoded JSON |

## Configuration

License resource limit keys:

| Key | Default | Purpose |
|---|---|---|
| `max_users` | -1 (unlimited) | Maximum users |
| `max_servers` | -1 (unlimited) | Maximum servers |
| `max_file_size_mb` | -1 (unlimited) | Maximum file upload size in MB |

## Related code

- `src/utils/common_utils/utils/licensing/__init__.py:151` — `setup()` entry point
- `src/utils/common_utils/utils/licensing/__init__.py:392` — `has_feature()` feature check
- `src/utils/common_utils/utils/licensing/__init__.py:591` — `generate_test_license_dict()` for CI
- `src/utils/common_utils/utils/licensing/core.py:135` — `LicenseManager` class
- `src/utils/common_utils/utils/licensing/core.py:267` — `validate()` with Ed25519 verification
- `src/utils/common_utils/utils/licensing/core.py:594` — `generate_keypair()` / `sign_license()`
