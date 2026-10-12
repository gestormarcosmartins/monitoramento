import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


logger = logging.getLogger("central-monitor-api")
_CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class InvalidField(BaseModel):
    field: str
    message: str
    type: str


class ErrorResponse(BaseModel):
    code: str = Field(description="Stable machine-readable error code")
    message: str
    detail: Any
    invalid_fields: list[InvalidField] = Field(default_factory=list)
    correlation_id: str
    retryable: bool


def openapi_error_responses() -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "Invalid request",
        401: "Authentication required",
        403: "Insufficient capability or object scope",
        404: "Object not found or intentionally concealed",
        409: "State, version or idempotency conflict",
        422: "Field validation failed",
        428: "Required precondition was not supplied",
        429: "Rate limit reached",
        500: "Unexpected internal failure",
        502: "Upstream integration failure",
        503: "Required dependency unavailable",
        504: "Upstream integration timeout",
    }
    return {code: {"model": ErrorResponse, "description": description} for code, description in descriptions.items()}


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", str(uuid4()))


def _error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        428: "precondition_required",
        429: "rate_limited",
        502: "upstream_failure",
        503: "service_unavailable",
        504: "upstream_timeout",
    }.get(status_code, "request_failed" if status_code < 500 else "internal_error")


def _retryable(status_code: int) -> bool:
    return status_code in (408, 425, 429, 502, 503, 504) or status_code >= 500


def _payload(request: Request, status_code: int, detail: Any, *, invalid_fields: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if isinstance(detail, str):
        message = detail
    elif status_code >= 500:
        message = "A dependência necessária está indisponível."
    else:
        message = "Não foi possível concluir a requisição."
    return {
        "code": _error_code(status_code),
        "message": message,
        # Kept for backwards compatibility while clients migrate to `message`.
        "detail": detail,
        "invalid_fields": invalid_fields or [],
        "correlation_id": _correlation_id(request),
        "retryable": _retryable(status_code),
    }


def install_error_contract(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        supplied = request.headers.get("X-Correlation-ID", "")
        request.state.correlation_id = supplied if _CORRELATION_PATTERN.fullmatch(supplied) else str(uuid4())
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=_payload(request, error.status_code, error.detail),
            headers=error.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part not in ("body", "query", "path", "header")),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_payload(request, 422, "Verifique os campos informados.", invalid_fields=fields),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled_request_error", extra={"correlation_id": _correlation_id(request)})
        return JSONResponse(
            status_code=500,
            content=_payload(request, 500, "Ocorreu uma falha interna."),
        )
