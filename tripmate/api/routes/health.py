"""Health check routes."""

import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from tripmate.api.dependencies import (
    get_application_readiness,
    get_checkpointer_resources,
)
from tripmate.core.logging import get_logger
from tripmate.persistence.checkpointer import CheckpointerResources

router = APIRouter()
logger = get_logger(__name__)

READINESS_TIMEOUT_SECONDS = 1.5


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "message": "VoyageCrew API is running",
        "features": [
            "supervisor_agent",
            "input_guardrail",
            "human_in_the_loop",
        ],
    }


@router.get("/health/live")
async def liveness_check():
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness_check(
    is_ready: bool = Depends(get_application_readiness),
    resources: CheckpointerResources | None = Depends(
        get_checkpointer_resources
    ),
):
    if not is_ready or resources is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready"},
        )

    try:
        probe = asyncio.get_running_loop().run_in_executor(
            None,
            resources.is_available,
        )
        available = await asyncio.wait_for(
            probe,
            timeout=READINESS_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.error(
            "PostgreSQL checkpointer readiness probe failed.",
            extra={
                "event": "health.readiness.failed",
                "integration": "postgresql",
                "outcome": "failure",
            },
        )
        available = False

    if not available:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready"},
        )
    return {"status": "ok"}
