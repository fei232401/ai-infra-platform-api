import logging
from typing import Any

from asyncpg import exceptions as pg_errors
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from ..domain import DomainError

logger = logging.getLogger("app.error")

SQLSTATE_MAP: dict[str, tuple[int, str]] = {
    "23505": (409, "unique_violation"),
    "23503": (422, "foreign_key_violation"),
    "23514": (422, "check_violation"),
    "23502": (422, "not_null_violation"),
}

EXCEPTION_MAP: tuple[tuple[type[BaseException], tuple[int, str]], ...] = (
    (pg_errors.UniqueViolationError, (409, "unique_violation")),
    (pg_errors.ForeignKeyViolationError, (422, "foreign_key_violation")),
    (pg_errors.CheckViolationError, (422, "check_violation")),
    (pg_errors.NotNullViolationError, (422, "not_null_violation")),
)


def classify_integrity_error(origin: Any) -> tuple[int, str, str]:
    sqlstate = getattr(origin, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate in SQLSTATE_MAP:
        status_code, code = SQLSTATE_MAP[sqlstate]
        return status_code, code, _message(origin)

    for exc_type, (status_code, code) in EXCEPTION_MAP:
        if isinstance(origin, exc_type):
            return status_code, code, _message(origin)

    return 409, "integrity_violation", _message(origin)


def _message(origin: Any) -> str:
    text = getattr(origin, "message", None) or str(origin)
    return text.splitlines()[0][:300]


def _error_response(
    status_code: int,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": detail or {}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        logger.info(
            "domain_error code=%s path=%s detail=%s", exc.code, request.url.path, exc.detail
        )
        return _error_response(exc.status_code, exc.code, exc.message, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            422,
            "request_invalid",
            "请求参数校验失败",
            {"errors": exc.errors()},
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        status_code, code, message = classify_integrity_error(exc.orig)
        logger.warning(
            "integrity_error code=%s path=%s origin=%r", code, request.url.path, exc.orig
        )
        return _error_response(
            status_code,
            code,
            message,
            {"sqlstate": str(getattr(exc.orig, "sqlstate", ""))},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error path=%s", request.url.path)
        return _error_response(500, "internal_error", "服务内部错误")
