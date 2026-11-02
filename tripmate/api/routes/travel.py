#Travel planning and approval API routes.

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from tripmate.api.dependencies import get_current_user_id, get_travel_service
from tripmate.api.schemas.travel import ApprovalRequest, TravelRequest
from tripmate.core.exceptions import BadRequestError
from tripmate.persistence.ownership import ThreadOwnershipStore
from tripmate.services.travel_service import TravelService

router = APIRouter()


@router.post("/api/travel")
async def travel_planner(
    request: Request,
    request_data: TravelRequest,
    user_id=Depends(get_current_user_id),
    travel_service: TravelService = Depends(get_travel_service),
):
    user_message = request_data.message.strip()
    if not user_message:
        raise BadRequestError("Message cannot be empty.")

    ownership = ThreadOwnershipStore(
        request.app.state.checkpointer_resources.pool
    )
    thread_id = request_data.thread_id
    if thread_id:
        await run_in_threadpool(ownership.require_owner, thread_id, user_id)
    else:
        thread_id = f"user_{uuid.uuid4().hex}"
        await run_in_threadpool(ownership.create_thread, thread_id, user_id)

    result = await run_in_threadpool(
        travel_service.start,
        user_input=user_message,
        thread_id=thread_id,
    )
    return JSONResponse(content={"success": True, **result})


@router.post("/api/travel/approve")
async def approve_travel_plan(
    request: Request,
    request_data: ApprovalRequest,
    user_id=Depends(get_current_user_id),
    travel_service: TravelService = Depends(get_travel_service),
):
    if not request_data.approved and not request_data.feedback.strip():
        raise BadRequestError(
            "Please provide revision feedback when rejecting the draft."
        )

    ownership = ThreadOwnershipStore(
        request.app.state.checkpointer_resources.pool
    )
    await run_in_threadpool(
        ownership.require_owner, request_data.thread_id, user_id
    )
    result = await run_in_threadpool(
        travel_service.resume,
        thread_id=request_data.thread_id,
        approved=request_data.approved,
        feedback=request_data.feedback,
    )
    return JSONResponse(content={"success": True, **result})
