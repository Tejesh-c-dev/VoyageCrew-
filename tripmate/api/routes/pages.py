"""Page and favicon routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from tripmate.core.config import PROJECT_ROOT

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )


@router.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})
