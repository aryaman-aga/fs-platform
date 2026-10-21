"""Consistent error shape: ``{"error": {"code", "message", "correlation_id"}}`` (08 §4:
never raw stack traces; an error code that links to the run log)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("fsweb.errors")


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, *, headers: dict[str, str] | None = None, extra: dict[str, Any] | None = None) -> None:
        super().__init__(status_code=status, detail=message, headers=headers)
        self.code = code
        self.extra = extra or {}


def bad_request(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(400, code, message, extra=extra)


def unauthorized(message: str = "authentication required", code: str = "unauthenticated") -> ApiError:
    return ApiError(401, code, message)


def forbidden(message: str = "not allowed", code: str = "forbidden", **extra: Any) -> ApiError:
    return ApiError(403, code, message, extra=extra)


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def conflict(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(409, code, message, extra=extra)


def precondition_failed(message: str = "the resource has changed; reload and retry") -> ApiError:
    return ApiError(412, "precondition_failed", message)


def unprocessable(code: str, message: str, **extra: Any) -> ApiError:
    return ApiError(422, code, message, extra=extra)


def too_many(message: str = "too many requests") -> ApiError:
    return ApiError(429, "rate_limited", message, headers={"Retry-After": "60"})


def _cid(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _body(code: str, message: str, request: Request, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "correlation_id": _cid(request)}
    if extra:
        err["details"] = extra
    return {"error": err}


_STATUS_CODES = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                 409: "conflict", 412: "precondition_failed", 413: "payload_too_large", 415: "unsupported_media_type",
                 422: "validation_error", 428: "precondition_required", 429: "rate_limited"}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, str(exc.detail), request, exc.extra), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "error")
        return JSONResponse(_body(code, str(exc.detail), request), status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(_body("validation_error", "request validation failed", request, {"problems": problems}), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"correlation_id": _cid(request), "path": request.url.path})
        return JSONResponse(_body("internal_error", "an internal error occurred; quote the correlation id to support", request), status_code=500)
