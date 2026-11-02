"""FastAPI dependencies for initialized application services."""

from uuid import UUID

from fastapi import Header, Request

from tripmate.api.auth import verify_clerk_token
from tripmate.core.exceptions import ServiceUnavailableError
from tripmate.persistence.checkpointer import CheckpointerResources
from tripmate.persistence.ownership import ThreadOwnershipStore
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


def get_current_user_id(
    request: Request,
    authorization: str | None = Header(default=None),
) -> UUID:
    settings = getattr(request.app.state, "settings", None)
    resources = getattr(request.app.state, "checkpointer_resources", None)
    if settings is None or resources is None:
        raise ServiceUnavailableError()
    identity = verify_clerk_token(authorization, settings)
    return ThreadOwnershipStore(resources.pool).get_or_create_user(identity)
