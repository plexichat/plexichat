import sqlite3
import threading
from typing import Any, Union

DbConnection = Union[sqlite3.Connection, Any]
DbCursor = Union[sqlite3.Cursor, Any]


class DatabaseLocal(threading.local):
    def __init__(self):
        pass
