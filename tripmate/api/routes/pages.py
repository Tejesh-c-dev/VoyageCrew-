"""Runtime configuration and React frontend routes."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from tripmate.core.config import PROJECT_ROOT, load_settings

router = APIRouter()


@router.get("/api/config")
async def public_config(request: Request):
    """Return browser-safe settings that must not be embedded in a build."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = load_settings(PROJECT_ROOT)
    return {
        "clerk_publishable_key": settings.clerk_publishable_key or "",
        "clerk_jwt_template": settings.clerk_jwt_template,
    }


@router.get("/")
async def home():
    """Serve the production React entry point when it has been built."""
    frontend_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if frontend_index.is_file():
        return FileResponse(frontend_index, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "Frontend build not found"})


@router.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})
