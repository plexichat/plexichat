# AGENTS.md - PlexiChat Development Guide

## Setup
```bash
python -m venv .venv                    # Create virtual environment
.venv\Scripts\activate                  # Windows activation
source .venv/bin/activate               # Linux/Mac activation
pip install -r requirements.txt         # Install dependencies
pip install -r requirements-test.txt    # Install test dependencies
git submodule update --init --recursive # Initialize submodules (common-utils)
```

## Commands
- **Run dev server**: `python main.py` (starts on http://localhost:8000)
- **Run tests**: `pytest -v` or `pytest src/tests/unit/` (unit only)
- **Run lint**: `ruff check src/` and `ruff format --check src/`
- **Type check**: `pyright src/` (single repo) or `python scripts/type_check_all.py` (all repos with reports)
- **Build**: N/A (Python, no build step required)

## Tech Stack
- **Framework**: FastAPI + Uvicorn (async web server)
- **Database**: SQLite (default) or PostgreSQL, with aiosqlite for async
- **Caching**: Redis (optional), in-memory fallback
- **WebSocket**: Native WebSocket gateway for real-time events
- **Auth**: Argon2 password hashing, session tokens, TOTP 2FA
- **Media**: Database-stored avatars/icons, optional S3/MinIO storage
- **Testing**: pytest with fixtures, markers for test categories

## Architecture
- `main.py` - Entry point, initializes all modules
- `src/api/` - FastAPI routes, schemas, middleware, WebSocket gateway
- `src/core/` - Business logic modules (auth, messaging, servers, presence, etc.)
- `src/tests/` - Test suite organized by module
- `src/utils/` - Shared utilities (logger, config, validation, encryption)
- `scripts/` - Development tools (type checking across all repos)
- Data stored in `~/.plexichat/` by default

## Conventions
- Python 3.11+, async/await throughout
- Type hints enforced via Pyright
- No trailing comments unless complex logic
- Virtual env in `.venv` (per gitignore)
- Pydantic for validation/serialization
- Module structure: `manager.py` (logic), `models.py` (data), `schema.py` (API), `exceptions.py`
