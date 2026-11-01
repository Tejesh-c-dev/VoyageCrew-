"""FastAPI dependencies for initialized application services."""

from fastapi import Request

from tripmate.core.exceptions import ServiceUnavailableError
from tripmate.persistence.checkpointer import CheckpointerResources
from tripmate.services.travel_service import TravelService


def get_application_readiness(request: Request) -> bool:
    return bool(getattr(request.app.state, "is_ready", False))


def get_checkpointer_resources(
    request: Request,
) -> CheckpointerResources | None:
    return getattr(request.app.state, "checkpointer_resources", None)


def get_travel_service(request: Request) -> TravelService:
    travel_service = getattr(request.app.state, "travel_service", None)
    if travel_service is None:
        raise ServiceUnavailableError()
    return travel_service
