"""Application-wide sanitized exception handling."""

from collections.abc import Mapping

import psycopg
from groq import APIError as GroqAPIError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tripmate.core.logging import correlation_context, get_logger
from tripmate.core.exceptions import ApplicationError, DatabaseUnavailableError

logger = get_logger(__name__)

_HTTP_ERROR_MESSAGES = {
    400: "The request is invalid.",
    401: "Authentication is required.",
    403: "The request is not permitted.",
    404: "The requested resource was not found.",
    405: "The request method is not supported.",
    422: "The request is invalid.",
}


def _error_response(
    status_code: int,
    message: str,
    headers: Mapping[str, str] | None = None,
    request: Request | None = None,
    content: dict | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    correlation_id = (
        getattr(request.state, "correlation_id", None)
        if request is not None
        else None
    )
    if correlation_id:
        response_headers["X-Request-ID"] = correlation_id
    return JSONResponse(
        status_code=status_code,
        content=content or {"success": False, "error": message},
        headers=response_headers or None,
    )


def _log_exception(
    event: str,
    exception: Exception,
    request: Request,
) -> None:
    correlation_id = getattr(request.state, "correlation_id", None)
    with correlation_context(correlation_id):
        logger.error(
            "Application request failed.",
            extra={
                "event": event,
                "error_type": type(exception).__name__,
                "error_code": getattr(exception, "error_code", None),
                "outcome": "failure",
            },
        )


async def handle_application_error(
    request: Request,
    exception: ApplicationError,
) -> JSONResponse:
    _log_exception("api.application_error", exception, request)
    headers = {"WWW-Authenticate": "Bearer"} if exception.status_code == 401 else None
    content = {"success": False, "error": exception.public_message}
    if exception.error_code:
        content["code"] = exception.error_code
    return _error_response(
        exception.status_code,
        exception.public_message,
        headers=headers,
        request=request,
        content=content,
    )


async def handle_database_error(
    request: Request,
    exception: psycopg.Error,
) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None)
    diagnostic = exception.diag
    with correlation_context(correlation_id):
        logger.error(
            "Application request failed.",
            extra={
                "event": "api.database_error",
                "operation": getattr(exception, "_tripmate_operation", None),
                "phase": getattr(exception, "_tripmate_phase", None),
                "error_type": type(exception).__name__,
                "sqlstate": exception.sqlstate,
                "message_primary": (
                    diagnostic.message_primary if diagnostic else None
                ),
                "table_name": diagnostic.table_name if diagnostic else None,
                "constraint_name": (
                    diagnostic.constraint_name if diagnostic else None
                ),
            },
        )
    safe_error = DatabaseUnavailableError()
    return _error_response(
        safe_error.status_code,
        safe_error.public_message,
        request=request,
    )


async def handle_external_provider_error(
    request: Request,
    exception: GroqAPIError,
) -> JSONResponse:
    _log_exception("api.external_provider_error", exception, request)
    return _error_response(
        502,
        "An external service is temporarily unavailable.",
        request=request,
    )


async def handle_validation_error(
    request: Request,
    _exception: RequestValidationError,
) -> JSONResponse:
    return _error_response(422, "The request is invalid.", request=request)


async def handle_http_error(
    request: Request,
    exception: StarletteHTTPException,
) -> JSONResponse:
    message = _HTTP_ERROR_MESSAGES.get(
        exception.status_code,
        "The request could not be completed.",
    )
    return _error_response(
        exception.status_code,
        message,
        exception.headers,
        request,
    )


async def handle_unexpected_error(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    _log_exception("api.unexpected_error", exception, request)
    return _error_response(
        500,
        "An unexpected error occurred.",
        request=request,
    )


def install_exception_handlers(application: FastAPI) -> None:
    application.add_exception_handler(
        ApplicationError,
        handle_application_error,
    )
    application.add_exception_handler(
        psycopg.Error,
        handle_database_error,
    )
    application.add_exception_handler(
        GroqAPIError,
        handle_external_provider_error,
    )
    application.add_exception_handler(
        RequestValidationError,
        handle_validation_error,
    )
    application.add_exception_handler(
        StarletteHTTPException,
        handle_http_error,
    )
    application.add_exception_handler(
        Exception,
        handle_unexpected_error,
    )
