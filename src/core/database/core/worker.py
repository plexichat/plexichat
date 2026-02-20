import inspect
from functools import wraps


def with_db_worker(func):
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            import src.api as api

            db = api.get_db()
            try:
                return await func(*args, **kwargs)
            finally:
                if db:
                    db.close()

        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            import src.api as api

            db = api.get_db()
            try:
                return func(*args, **kwargs)
            finally:
                if db:
                    db.close()

        return sync_wrapper
