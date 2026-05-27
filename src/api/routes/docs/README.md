# Documentation Routes

Dynamic documentation system for Plexichat. Serves rendered API documentation, admin guides, configuration references, and end-user help content. Supports OpenAPI/Swagger output and customizable themes.

## Components

### `router.py`
Route registration for all documentation endpoints:
- `/docs` - Main documentation portal
- `/docs/api` - API reference (OpenAPI)
- `/docs/admin` - Admin documentation
- `/docs/guide` - User guides and tutorials

### `openapi.py`
OpenAPI/Swagger specification generation:
- Scans registered API routes for schema generation
- Builds OpenAPI 3.0 compliant JSON output
- Includes request/response schemas, auth flows, and error codes
- Handles path parameters, query params, and request bodies

### `renderer.py`
Content rendering pipeline:
- Markdown to HTML conversion
- Code syntax highlighting
- Table of contents generation
- Responsive HTML output with mobile support

### `navigation.py`
Documentation navigation structure:
- Hierarchical sidebar generation
- Breadcrumb trails
- Cross-reference linking
- Search index for docs content

### `config.py`
Documentation configuration:
- Site title, version, description
- Theme selection
- Default language/locale
- Brand logo and favicon

### `dynamic.py`
Dynamic content generation:
- API endpoint examples with live curl commands
- Auto-generated changelog from git history
- Server version and build information
- Rate limit and feature availability info

### `theme.py`
Documentation theming:
- Light/dark mode support
- Color scheme customization
- Typography settings
- Layout options (full-width, sidebar)
