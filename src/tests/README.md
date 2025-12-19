# PlexiChat Test Suite

## Quick Start

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests (excluding slow)
pytest

# Run specific module tests
pytest -m auth
pytest -m messaging
pytest -m servers

# Run in parallel (much faster!)
pytest -n auto

# Run with coverage
pytest --cov=src --cov-report=html
```

## Key Concepts

### Session-Scoped Database

Instead of creating a new database for each test file (slow!), we create ONE database per test session. Tests are isolated using transaction rollback.

```python
# OLD (slow) - each test file created its own DB
@pytest.fixture(scope="module")
def db_and_auth(test_env, request):
    db_path = os.path.join(test_env, f"test_{module_name}.db")
    # ... create new DB, initialize modules ...

# NEW (fast) - shared session DB with transaction isolation
@pytest.fixture
def db(db_manager):
    db_manager.begin_transaction()
    yield db_manager.db
    db_manager.rollback_transaction()
```

### Lazy Module Loading

Modules are only initialized when first accessed:

```python
def test_something(modules):
    # Only auth is loaded here
    user = modules.auth.register(...)
    
    # Now messaging is also loaded
    dm = modules.messaging.create_dm(...)
```

### Factory Fixtures

Use factories instead of creating entities manually:

```python
# OLD
user = auth.register(
    username=f"user_{uuid.uuid4().hex[:8]}",
    email=f"user_{uuid.uuid4().hex[:8]}@example.com",
    password="TestPass123!"
)

# NEW
user = user_factory.create()  # Fast, uses pre-created pool
user = user_factory.create(username="specific_name")  # Custom
user, token = user_factory.create_with_login()  # With auth token
```

## Writing Tests

### Basic Test Pattern

```python
import pytest

@pytest.mark.auth  # Module marker
@pytest.mark.integration  # Test type marker
class TestSomething:
    
    def test_basic_operation(self, modules, user_factory):
        """Test description."""
        user = user_factory.create()
        result = modules.auth.some_operation(user.id)
        assert result is not None
```

### Parameterized Tests

Use parameterization for better coverage with less code:

```python
@pytest.mark.parametrize("invalid_input,reason", [
    ("", "empty"),
    ("   ", "whitespace"),
    ("x" * 1000, "too long"),
])
def test_rejects_invalid_input(self, invalid_input, reason, modules):
    with pytest.raises(ValidationError):
        modules.something.validate(invalid_input)
```

### Convenience Fixtures

```python
def test_with_user(self, test_user):
    # Single user
    assert test_user.id is not None

def test_with_two_users(self, two_users):
    user1, user2 = two_users
    
def test_with_server(self, test_server):
    server, owner = test_server
    
def test_with_dm(self, test_dm):
    dm, user1, user2 = test_dm
```

## Test Markers

| Marker | Description | Command |
|--------|-------------|---------|
| `unit` | Fast tests, no DB | `pytest -m unit` |
| `integration` | Full module tests | `pytest -m integration` |
| `slow` | Time-dependent tests | `pytest -m slow` |
| `auth` | Auth module | `pytest -m auth` |
| `messaging` | Messaging module | `pytest -m messaging` |
| `servers` | Servers module | `pytest -m servers` |
| `api` | API routes | `pytest -m api` |

## Running Tests

```bash
# Fast feedback during development
pytest -x -q  # Stop on first failure, quiet output

# Run specific test file
pytest src/tests/auth/test_login.py

# Run specific test
pytest src/tests/auth/test_login.py::TestLogin::test_login_success

# Run with verbose output
pytest -v

# Run with timing info (find slow tests)
pytest --durations=20

# Run in parallel
pytest -n 4      # 4 workers
pytest -n auto   # Auto-detect CPUs
```

## Performance Tips

1. **Use the pool**: `user_factory.create()` uses pre-created users
2. **Avoid `use_pool=False`** unless you need specific usernames
3. **Use parameterization** instead of multiple similar tests
4. **Mark slow tests** with `@pytest.mark.slow`
5. **Run in parallel** with `pytest -n auto`

## Migration Guide

If you're updating old tests to use the new infrastructure:

### Before (Old Pattern)
```python
@pytest.fixture(scope="module")
def db_and_auth(test_env, request):
    # ... lots of setup code ...
    db = Database()
    db.connect()
    auth.setup(db)
    yield db, auth
    db.close()

def test_something(db_and_auth):
    db, auth = db_and_auth
    user = auth.register("user", "email@test.com", "Pass123!")
```

### After (New Pattern)
```python
def test_something(modules, user_factory):
    user = user_factory.create()
    # modules.auth is available if needed
```

### Legacy Compatibility

The old fixtures still work during migration:
- `db_and_auth` → Use `modules.auth` instead
- `db_and_modules` → Use `modules` instead
- `registered_user` → Use `user_factory.create()` instead
