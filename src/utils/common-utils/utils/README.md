# Utils

Core utility modules for the common-utils library.

## Modules

- `config/` - Configuration file loading and management (YAML/JSON)
- `logger/` - Logging utility with rotation and zipping support
- `validator/` - Input validation with SQL injection and XSS protection
- `version/` - Version string parsing, comparison, and management

## Usage

```python
from utils.config import ConfigLoader
from utils.logger import Logger
from utils.validator import Validator
from utils.version import parse_version, compare_versions
```

Each module is self-contained with its own README for detailed documentation.
