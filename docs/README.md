# PlexiChat Documentation

This directory contains the narrative backend documentation served by the custom docs portal.

## Primary Entry Points

- `index.md` — portal home page
- `getting-started.md` — first-call and capability guidance
- `configuration.md` — high-level configuration areas
- `deployment.md` — non-sensitive deployment guidance
- `rate-limits.md` — request-throttling overview
- `features.md` — backend feature map
- `security.md` — public security guidance
- `api/index.md` — REST route-group overview
- `websocket/index.md` — gateway overview

## Additional Reference Pages

The `api/` and `websocket/` subdirectories contain route-group and gateway-specific documentation, including the pages added for search, notifications, polls, voice, media, reports, feedback, telemetry, system routes, and gateway opcodes.

## Documentation Rules For This Directory

- keep examples aligned with the codebase
- prefer runtime placeholders such as `{{BASE_URL}}` and `{{WEBSOCKET_URL}}`
- avoid private infrastructure details, secrets, and environment-specific runbooks
- use the generated OpenAPI docs at `/docs` for exact schemas when narrative docs would duplicate them