"""Runtime configuration and React frontend routes."""

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from tripmate.core.config import PROJECT_ROOT

router = APIRouter()


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
