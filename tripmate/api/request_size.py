"""Bounded request-body protection for the travel planning endpoint."""

from starlette.responses import JSONResponse

from tripmate.core.logging import get_logger

logger = get_logger(__name__)
MAX_TRAVEL_REQUEST_SIZE = 64 * 1024
_TRAVEL_PATHS = {"/api/travel", "/api/travel/approve"}
_OVERSIZED_MESSAGE = "The travel request body exceeds the 64 KiB limit."


class TravelRequestSizeLimitMiddleware:
    """Reject oversized travel JSON requests before application routing."""

    def __init__(self, app, max_size: int = MAX_TRAVEL_REQUEST_SIZE):
        self.app = app
        self.max_size = max_size

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in _TRAVEL_PATHS
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        declared_size = None
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
        if declared_size is not None and declared_size > self.max_size:
            await self._reject(scope, send, declared_size)
            return

        messages = []
        request_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                request_size += len(message.get("body", b""))
                if request_size > self.max_size:
                    await self._reject(scope, send, request_size)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        message_index = 0

        async def replay_receive():
            nonlocal message_index
            if message_index < len(messages):
                current = messages[message_index]
                message_index += 1
                return current
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _reject(self, scope, send, request_size: int):
        logger.warning(
            "Travel request body exceeded configured size limit.",
            extra={
                "event": "request.body_too_large",
                "error_code": "REQUEST_BODY_TOO_LARGE",
                "method": scope.get("method", ""),
                "path": scope.get("path", ""),
                "status_code": 413,
                "request_size_bytes": request_size,
                "configured_limit_bytes": self.max_size,
                "outcome": "failure",
            },
        )
        response = JSONResponse(
            status_code=413,
            content={
                "success": False,
                "code": "REQUEST_BODY_TOO_LARGE",
                "error": _OVERSIZED_MESSAGE,
            },
        )
        await response(scope, _empty_receive, send)


async def _empty_receive():
    return {"type": "http.disconnect"}
