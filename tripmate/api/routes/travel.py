#Travel planning and approval API routes.

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from tripmate.api.dependencies import get_travel_service
from tripmate.api.schemas.travel import ApprovalRequest, TravelRequest
from tripmate.core.exceptions import BadRequestError
from tripmate.services.travel_service import TravelService

router = APIRouter()


@router.post("/api/travel")
async def travel_planner(
    request_data: TravelRequest,
    travel_service: TravelService = Depends(get_travel_service),
):
    user_message = request_data.message.strip()
    if not user_message:
        raise BadRequestError("Message cannot be empty.")

    result = await run_in_threadpool(
        travel_service.start,
        user_input=user_message,
        thread_id=request_data.thread_id,
    )
    return JSONResponse(content={"success": True, **result})


@router.post("/api/travel/approve")
async def approve_travel_plan(
    request_data: ApprovalRequest,
    travel_service: TravelService = Depends(get_travel_service),
):
    if not request_data.approved and not request_data.feedback.strip():
        raise BadRequestError(
            "Please provide revision feedback when rejecting the draft."
        )

    result = await run_in_threadpool(
        travel_service.resume,
        thread_id=request_data.thread_id,
        approved=request_data.approved,
        feedback=request_data.feedback,
    )
    return JSONResponse(content={"success": True, **result})
