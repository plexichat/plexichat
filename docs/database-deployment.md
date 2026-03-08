# Database Deployment Note

Detailed database deployment procedures are intentionally not published in the served backend documentation.

## Why

Database migrations, backup strategy, restoration steps, and host-specific operational commands are environment-specific and should remain in internal runbooks.

## What Is Safe To Rely On Here

- the backend exposes `/health`, `/api/v1/version`, and `/api/v1/status` for runtime checks
- the generated OpenAPI docs describe request and response contracts
- public docs in this portal describe application behavior, not infrastructure procedures
