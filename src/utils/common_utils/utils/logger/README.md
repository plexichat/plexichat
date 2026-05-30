# Logger Utility

A simple, configurable logging utility for Python applications.

## Features

- **Automatic Log Rotation**: Rotates logs based on size.
- **Log Zipping**: Automatically zips old log files on startup.
- **Customizable Naming**: Define your own log filename format.
- **Latest Log**: Always maintains a `latest.log` file with the most recent logs.
- **Easy Integration**: Designed to be initialized once in your main application.

## Installation

Ensure this directory is in your Python path.

## Usage

### Setup (Once in main.py)

In your main application file (e.g., `main.py`), setup the logger once:

```python
import utils.logger as logger

# Setup - do this ONCE in your main file
logger.setup(log_dir="logs", level="DEBUG")

# Use it immediately
logger.info("Application started")
logger.error("Something went wrong")
```

### Usage (In any other file)

In any other file in your project, just import and use:

```python
import utils.logger as logger

def do_work():
    logger.info("Starting work...")
    logger.debug("Processing data")
    logger.error("Something failed!")
```

**No need to pass logger objects around or configure again!**

### Legacy Usage (Still Supported)

You can also use the Logger class directly if you need multiple independent loggers:

```python
from utils.logger import Logger
import logging

log_manager = Logger(
    log_dir="logs",
    log_name_format="myapp_%Y-%m-%d.log",
    level=logging.DEBUG,
    zip_logs=True
)
logger = log_manager.get_logger()
logger.info("Application started")
```

### Configuration Options

| Option            | Description                         | Default                       |
| ----------------- | ----------------------------------- | ----------------------------- |
| `log_dir`         | Directory to store logs             | (Required)                    |
| `log_name_format` | Format for log filenames (strftime) | `"app_%Y-%m-%d_%H-%M-%S.log"` |
| `max_bytes`       | Max size before rotation            | `10MB`                        |
| `backup_count`    | Number of backups to keep           | `5`                           |
| `level`           | Logging level                       | `logging.INFO`                |
| `zip_logs`        | Zip old logs on startup             | `True`                        |
| `rotate`          | Enable rotation                     | `True`                        |
| `max_zip_age_days`| Delete zipped logs older than X days| `30`                          |

### Accessing Logs

You can retrieve a list of all log files (including zips) programmatically:

```python
files = log_manager.get_logs()
print(files)
```

## Notes

- The `latest.log` file in the log directory will always contain the logs from the current session.
- Old `.log` files are zipped into `.log.zip` files if `zip_logs` is enabled.
