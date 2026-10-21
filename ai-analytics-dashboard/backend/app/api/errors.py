import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


def api_error(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        detail = exc.detail
    else:
        default_codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        detail = {"code": default_codes.get(exc.status_code, "error"), "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=getattr(exc, "headers", None))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
    msg = first.get("msg", "Invalid input")
    message = f"{loc}: {msg}" if loc else msg
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": message}})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500, content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
    )
