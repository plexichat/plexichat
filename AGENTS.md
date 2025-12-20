# Agent Guide

## Setup
```bash
python -m venv .venv
.venv\Scripts\activate  # Windows: or source .venv/bin/activate on Linux/Mac
pip install -r requirements.txt
git submodule update --init --recursive
```

## Commands
- **Dev Server**: `python main.py`
- **Tests**: `pytest src/tests/` (install: `pip install -r requirements-test.txt`)
- **Lint**: `ruff check src/` (install: `pip install ruff`)
- **Format**: `ruff format src/`

## Tech Stack
- **Framework**: FastAPI + Uvicorn (async Python web)
- **Database**: SQLite (default) or PostgreSQL + aiosqlite
- **WebSocket**: Native FastAPI WebSocket gateway
- **Auth**: Argon2 password hashing + TOTP 2FA
- **Storage**: Local filesystem, S3/MinIO, or database BLOBs

## Architecture
- `main.py` - Entry point, server lifecycle management
- `src/api/` - FastAPI routes, schemas, middleware, WebSocket gateway
- `src/core/` - Business logic modules (auth, messaging, servers, etc.)
- `src/utils/` - Shared utilities (logger, config, encryption)
- Data stored in `~/.plexichat/` (database, logs, media, temp)

## Conventions
- Use async/await for I/O operations
- Type hints required (Pydantic models for validation)
- Follow existing module structure (setup/teardown pattern)
- No comments unless complex logic requires explanation
