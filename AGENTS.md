1→# AGENTS.md - PlexiChat Development Guide
2→
3→## Setup
4→```bash
5→python -m venv .venv                    # Create virtual environment
6→.venv\Scripts\activate                  # Windows activation
7→source .venv/bin/activate               # Linux/Mac activation
8→pip install -r requirements.txt         # Install dependencies
9→pip install -r requirements-test.txt    # Install test dependencies
10→git submodule update --init --recursive # Initialize submodules (common-utils)
11→```
12→
13→## Commands
14→- **Run dev server**: `python main.py` (starts on http://localhost:8000)
15→- **Run tests**: `pytest -v` or `pytest src/tests/unit/` (unit only)
16→- **Run lint**: `ruff check src/` and `ruff format --check src/`
17→- **Type check**: `pyright src/`
18→- **Build**: N/A (Python, no build step required)
19→
20→## Tech Stack
21→- **Framework**: FastAPI + Uvicorn (async web server)
22→- **Database**: SQLite (default) or PostgreSQL, with aiosqlite for async
23→- **Caching**: Redis (optional), in-memory fallback
24→- **WebSocket**: Native WebSocket gateway for real-time events
25→- **Auth**: Argon2 password hashing, session tokens, TOTP 2FA
26→- **Media**: Database-stored avatars/icons, optional S3/MinIO storage
27→- **Testing**: pytest with fixtures, markers for test categories
28→
29→## Architecture
30→- `main.py` - Entry point, initializes all modules
31→- `src/api/` - FastAPI routes, schemas, middleware, WebSocket gateway
32→- `src/core/` - Business logic modules (auth, messaging, servers, presence, etc.)
33→- `src/tests/` - Test suite organized by module
34→- `src/utils/` - Shared utilities (logger, config, validation, encryption)
35→- Data stored in `~/.plexichat/` by default
36→
37→## Conventions
38→- Python 3.11+, async/await throughout
39→- Type hints enforced via Pyright
40→- No trailing comments unless complex logic
41→- Virtual env in `.venv` (per gitignore)
42→- Pydantic for validation/serialization
43→- Module structure: `manager.py` (logic), `models.py` (data), `schema.py` (API), `exceptions.py`
44→