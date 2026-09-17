from typing import Any


class DomainError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class ValidationError(DomainError):
    status_code = 422
    code = "validation_failed"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"


class RateLimitExceededError(DomainError):
    status_code = 429
    code = "rate_limited"
