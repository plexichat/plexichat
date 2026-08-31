# Suite Registry

Contract-only module defining the PlexiSuite app registry interfaces and schema types for a future launcher-capable app catalog.

## Overview

The registry module establishes the data shapes and protocol contracts that a future API endpoint will expose to the PlexiSuite universal home launcher. It is intentionally decoupled from existing capability routes and does not perform authorization decisions or replace the capability service. The server controls app visibility; the client only renders.

## Architecture

```
┌─────────────────────────────────────┐
│         Future API Route            │
│    (capability-aware catalog)       │
├─────────────────────────────────────┤
│        SuiteRegistry Protocol       │
│  ┌───────────────────────────────┐  │
│  │ list_apps() → AppManifest[]  │  │
│  │ get_capability(f) → CapProj  │  │
│  └───────────────────────────────┘  │
├─────────────────────────────────────┤
│           Schema Types              │
│  AppManifest | CapabilityProjection │
│  SuiteRegistryResponse             │
└─────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `interfaces.py` | `SuiteRegistry` protocol — read-only contract for app listing and capability projection |
| `schema.py` | Frozen dataclasses: `AppManifest`, `CapabilityProjection`, `SuiteRegistryResponse` |

## Schema

### AppManifest

Server-visible metadata for one PlexiSuite application.

| Field | Type | Description |
|-------|------|-------------|
| `app_id` | `str` | Unique application identifier |
| `label` | `str` | Display name |
| `icon` | `str` | Icon reference |
| `color_token` | `str` | Theme color token |
| `artifact_types` | `List[str]` | Supported artifact types |
| `capability` | `Optional[str]` | Required capability name |
| `scopes` | `List[str]` | Required OAuth scopes |
| `supports_embedded` | `bool` | Can run embedded |
| `supports_offline` | `bool` | Available offline |
| `settings_path` | `Optional[str]` | Route to app settings |
| `onboarding_path` | `Optional[str]` | Route to app onboarding |

### CapabilityProjection

Safe capability summary for launcher rendering.

| Field | Type | Description |
|-------|------|-------------|
| `feature` | `str` | Feature identifier |
| `state` | `str` | Current state |
| `message` | `str` | Human-readable status |
| `details` | `Dict[str, Any]` | Additional details |

## Security Invariants

- Unknown app IDs and artifact types are rejected, never dynamically imported.
- Manifest paths are allow-listed and same-origin.
- Capability details must not disclose secrets or inaccessible resource names.
- Registry responses are bounded and cacheable per user/server capability scope.

## Related Code

- Capability routes: `src/api/routes/capabilities.py`
- Suite domain: `src/core/suite/__init__.py`
- PlexiStash (sibling module): `src/core/suite/stash/`
