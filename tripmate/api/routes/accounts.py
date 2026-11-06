"""Signup, login, logout, and session inspection routes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from tripmate.api.auth import AccountStore, SESSION_COOKIE, SESSION_LIFETIME
from tripmate.core.exceptions import AuthenticationError, ServiceUnavailableError

router = APIRouter()


def _store(request: Request) -> AccountStore:
    resources = getattr(request.app.state, "checkpointer_resources", None)
    if resources is None:
        raise ServiceUnavailableError()
    return AccountStore(resources.pool)


def _set_cookie(response: JSONResponse, request: Request, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=int(SESSION_LIFETIME.total_seconds()), httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/")


@router.post("/api/auth/signup")
async def signup(request: Request):
    body = await request.json()
    user_id, token = await run_in_threadpool(_store(request).signup, str(body.get("email", "")), str(body.get("password", "")))
    response = JSONResponse({"authenticated": True, "user_id": str(user_id)})
    _set_cookie(response, request, token)
    return response


@router.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    user_id, token = await run_in_threadpool(_store(request).login, str(body.get("email", "")), str(body.get("password", "")))
    response = JSONResponse({"authenticated": True, "user_id": str(user_id)})
    _set_cookie(response, request, token)
    return response


@router.post("/api/auth/logout")
async def logout(request: Request):
    await run_in_threadpool(_store(request).revoke, request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=request.url.scheme == "https", samesite="lax")
    return response


@router.get("/api/auth/session")
async def session(request: Request):
    user_id = await run_in_threadpool(_store(request).user_for_session, request.cookies.get(SESSION_COOKIE))
    return {"authenticated": True, "user_id": str(user_id)}
