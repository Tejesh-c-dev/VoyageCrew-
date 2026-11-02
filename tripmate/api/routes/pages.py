"""Page and favicon routes."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from tripmate.core.config import PROJECT_ROOT, load_settings

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


@router.get("/api/config")
async def public_config(request: Request):
    """Return browser-safe settings that must not be embedded in a build."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = load_settings(PROJECT_ROOT)
    return {"clerk_publishable_key": settings.clerk_publishable_key or ""}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    frontend_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if frontend_index.is_file():
        return FileResponse(frontend_index, media_type="text/html")
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = load_settings(PROJECT_ROOT)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "clerk_publishable_key": settings.clerk_publishable_key or "",
            "clerk_frontend_api_url": (
                settings.clerk_frontend_api_url or ""
            ).rstrip("/"),
        },
    )

@router.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})
