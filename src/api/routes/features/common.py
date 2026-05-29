from typing import NoReturn

from fastapi import HTTPException


def raise_bad_request(message: str) -> NoReturn:
    raise HTTPException(
        status_code=400, detail={"error": {"code": 400, "message": message}}
    )


def raise_forbidden(message: str) -> NoReturn:
    raise HTTPException(
        status_code=403, detail={"error": {"code": 403, "message": message}}
    )


def raise_not_found(message: str) -> NoReturn:
    raise HTTPException(
        status_code=404, detail={"error": {"code": 404, "message": message}}
    )


def raise_internal(message: str = "Internal server error") -> NoReturn:
    raise HTTPException(
        status_code=500, detail={"error": {"code": 500, "message": message}}
    )


def parse_id(value: str, field: str = "ID") -> int:
    try:
        return int(value)
    except ValueError:
        raise_bad_request(f"Invalid {field}")


def call_or_raise(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ValueError as e:
        raise_bad_request(str(e))
    except PermissionError as e:
        raise_forbidden(str(e))
